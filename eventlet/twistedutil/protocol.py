# Copyright (c) 2008-2009 AG Projects
# Author: Denis Bilenko
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

"""Basic twisted protocols converted to synchronous mode"""
import sys
from twisted.internet.protocol import Protocol as twistedProtocol
from twisted.internet.error import ConnectionDone
from twisted.internet.protocol import Factory, ClientFactory
from twisted.python import failure

from eventlet.api import spawn
from eventlet.coros import queue, event

class Producer2Event(object):

    # implements IPushProducer

    def __init__(self, event):
        self.event = event

    def resumeProducing(self):
        self.event.send(1)

    def pauseProducing(self):
        self.event.reset()

    def stopProducing(self):
        del self.event

class GreenTransportBase(object):

    write_event = None
    transportBufferSize = None

    def __init__(self, transportBufferSize=None):
        if transportBufferSize is not None:
            self.transportBufferSize = transportBufferSize

    def build_protocol(self):
        # note to subclassers: self._queue must have send and send_exception that never block
        self._queue = queue()
        protocol = self.protocol_class(self._queue)
        return protocol

    def _wait(self):
        self.transport.resumeProducing()
        try:
            return self._queue.wait()
        finally:
            self.transport.pauseProducing()

    def write(self, data):
        self.transport.write(data)
        if self.write_event is not None:
            self.write_event.wait()

    def __getattr__(self, item):
        if item=='transport':
            raise AttributeError(item)
        try:
            return getattr(self.transport, item)
        except AttributeError:
            me = type(self).__name__
            trans = type(self.transport).__name__
            raise AttributeError("Neither %r nor %r has attribute %r" % (me, trans, item))

    def resumeProducing(self):
        self.paused -= 1
        if self.paused==0:
            self.transport.resumeProducing()

    def pauseProducing(self):
        self.paused += 1
        if self.paused==1:
            self.transport.pauseProducing()

    def _init_transport_producer(self):
        self.transport.pauseProducing()
        self.paused = 1

    def _init_transport(self):
        transport = self._queue.wait()
        self.transport = transport
        if self.transportBufferSize is not None:
            transport.bufferSize = self.transportBufferSize
        self._init_transport_producer()
        if self.write_event is None:
            self.write_event = event()
            self.write_event.send(1)
        transport.registerProducer(Producer2Event(self.write_event), True)

class Protocol(twistedProtocol):

    def __init__(self, queue):
        self._queue = queue

    def connectionMade(self):
        self._queue.send(self.transport)

    def dataReceived(self, data):
        self._queue.send(data)

    def connectionLost(self, reason):
        self._queue.send_exception(reason.type, reason.value, reason.tb)
        del self._queue


class UnbufferedTransport(GreenTransportBase):
    """A very simple implementation of a green transport without an additional buffer"""

    protocol_class = Protocol

    def recv(self):
        """Receive a single chunk of undefined size.

        Return '' if connection was closed cleanly, raise the exception if it was closed
        in a non clean fashion. After that all successive calls return ''.
        """
        if self._queue is None:
            return ''
        try:
            return self._wait()
        except ConnectionDone:
            self._queue = None
            return ''
        except:
            self._queue = None
            raise

    def read(self):
        """Read the data from the socket until the connection is closed cleanly.

        If connection was closed in a non-clean fashion, the appropriate exception
        is raised. In that case already received data is lost.
        Next time read() is called it returns ''.
        """
        result = ''
        while True:
            recvd = self.recv()
            if not recvd:
                break
            result += recvd
        return result

    # iterator protocol:

    def __iter__(self):
        return self

    def next(self):
        result = self.recv()
        if not result:
            raise StopIteration
        return result


class GreenTransport(GreenTransportBase):

    protocol_class = Protocol
    _buffer = ''
    _error = None

    def _wait(self):
        # don't pause/resume producer here; read and recv methods will do it themselves
        return self._queue.wait()

    def read(self, size=-1):
        """Read size bytes or until EOF"""
        if self._queue is not None:
            resumed = False
            try:
                try:
                    while len(self._buffer) < size or size < 0:
                        if not resumed:
                            self.resumeProducing()
                            resumed = True
                        self._buffer += self._wait()
                except ConnectionDone:
                    self._queue = None
                except:
                    self._queue = None
                    self._error = sys.exc_info()
            finally:
                if resumed:
                    self.pauseProducing()
        if size>=0:
            result, self._buffer = self._buffer[:size], self._buffer[size:]
        else:
            result, self._buffer = self._buffer, ''
        if not result and self._error is not None:
            error, self._error = self._error, None
            raise error[0], error[1], error[2]
        return result

    def recv(self, buflen=None):
        """Receive a single chunk of undefined size but no bigger than buflen"""
        if self._queue is not None and not self._buffer:
            self.resumeProducing()
            try:
                try:
                    recvd = self._wait()
                    #print 'received %r' % recvd
                    self._buffer += recvd
                except ConnectionDone:
                    self._queue = None
                except:
                    self._queue = None
                    self._error = sys.exc_info()
            finally:
                self.pauseProducing()
        if buflen is None:
            result, self._buffer = self._buffer, ''
        else:
            result, self._buffer = self._buffer[:buflen], self._buffer[buflen:]
        if not result and self._error is not None:
            error = self._error
            self._error = None
            raise error[0], error[1], error[2]
        return result

    # iterator protocol:

    def __iter__(self):
        return self

    def next(self):
        res = self.recv()
        if not res:
            raise StopIteration
        return res


class GreenInstanceFactory(ClientFactory):

    def __init__(self, instance, event):
        self.instance = instance
        self.event = event

    def buildProtocol(self, addr):
        return self.instance

    def clientConnectionFailed(self, connector, reason):
        self.event.send_exception(reason.type, reason.value, reason.tb)


class GreenClientCreator(object):
    """Connect to a remote host and return a connected green transport instance.
    """

    gtransport_class = GreenTransport

    def __init__(self, reactor=None, gtransport_class=None, *args, **kwargs):
        if reactor is None:
            from twisted.internet import reactor
        self.reactor = reactor
        if gtransport_class is not None:
            self.gtransport_class = gtransport_class
        self.args = args
        self.kwargs = kwargs

    def _make_transport_and_factory(self):
        gtransport = self.gtransport_class(*self.args, **self.kwargs)
        protocol = gtransport.build_protocol()
        factory = GreenInstanceFactory(protocol, gtransport._queue)
        return gtransport, factory

    def connectTCP(self, host, port, *args, **kwargs):
        gtransport, factory = self._make_transport_and_factory()
        self.reactor.connectTCP(host, port, factory, *args, **kwargs)
        gtransport._init_transport()
        return gtransport

    def connectSSL(self, host, port, *args, **kwargs):
        gtransport, factory = self._make_transport_and_factory()
        self.reactor.connectSSL(host, port, factory, *args, **kwargs)
        gtransport._init_transport()
        return gtransport

    def connectTLS(self, host, port, *args, **kwargs):
        gtransport, factory = self._make_transport_and_factory()
        self.reactor.connectTLS(host, port, factory, *args, **kwargs)
        gtransport._init_transport()
        return gtransport

    def connectUNIX(self, address, *args, **kwargs):
        gtransport, factory = self._make_transport_and_factory()
        self.reactor.connectUNIX(address, factory, *args, **kwargs)
        gtransport._init_transport()
        return gtransport

    def connectSRV(self, service, domain, *args, **kwargs):
        SRVConnector = kwargs.pop('ConnectorClass', None)
        if SRVConnector is None:
            from twisted.names.srvconnect import SRVConnector
        gtransport, factory = self._make_transport_and_factory()
        c = SRVConnector(self.reactor, service, domain, factory, *args, **kwargs)
        c.connect()
        gtransport._init_transport()
        return gtransport


class SpawnFactory(Factory):
    """Factory that spawns a new greenlet for each incoming connection.

    For an incoming connection a new greenlet is created using the provided
    callback as a function and a connected green transport instance as an
    argument.
    """

    gtransport_class = GreenTransport

    def __init__(self, handler, gtransport_class=None, *args, **kwargs):
        self.handler = handler
        if gtransport_class is not None:
            self.gtransport_class = gtransport_class
        self.args = args
        self.kwargs = kwargs

    def buildProtocol(self, addr):
        gtransport = self.gtransport_class(*self.args, **self.kwargs)
        protocol = gtransport.build_protocol()
        protocol.factory = self
        spawn(self._spawn, gtransport, protocol)
        return protocol

    def _spawn(self, gtransport, protocol):
        try:
            gtransport._init_transport()
        except Exception:
            self._log_error(failure.Failure(), gtransport, protocol)
        else:
            spawn(self.handler, gtransport)

    def _log_error(self, failure, gtransport, protocol):
        from twisted.python import log
        log.msg('%s: %s' % (protocol.transport.getPeer(), failure.getErrorMessage()))
