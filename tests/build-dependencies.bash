#!/bin/bash -ex
curl_cache="$HOME/.cache/curl"
root=$(readlink -f $1)
PKG_CONFIG_PATH="$root/lib/pkgconfig"

if [[ -z "$root" ]]; then
	echo "$0 <fakeroot>" >&2
	exit 1
fi

install -d $root
install -d $curl_cache

# libssl-dev libmysqlclient-dev libpq-dev libzmq3-dev

# libssl
if [[ ! -e $root/include/openssl/ssl.h ]]; then
	path="$curl_cache/openssl.tar.gz"
	url="https://openssl.org/source/openssl-1.0.2.tar.gz"
	if [[ ! -e "$path" ]]; then
		curl -LS "$url" |tee "$path" |md5sum >$curl_cache/openssl.have.md5
		# TODO: verify md5 38373013fc85c790aabf8837969c5eba
	fi
	tar -xzf "$path"
	(
		cd openssl-1.0.2
		./config --prefix=$root no-shared -fPIC -DOPENSSL_PIC
		make
		make install
	)
fi

# libmysqlclient
if [ ! -e $root/include/mysql/mysql.h ]; then
	path="$curl_cache/libmysqlclient.deb"
	url="http://cdn.mysql.com/Downloads/MySQL-5.6/libmysqlclient18_5.6.23-1ubuntu14.10_amd64.deb"
	if [[ ! -e "$path" ]]; then
		curl -LS "$url" |tee "$path" |md5sum >$curl_cache/mysql.have.md5
		# TODO: verify md5
	fi
	install -d tmp
	(
		cd tmp
		ar x "$path"
		tar -xJf data.tar.xz
		cp -al usr/lib "$root/"
	)
	rm -rf ./tmp
	path="$curl_cache/libmysqlclient-dev.deb"
	url="http://cdn.mysql.com/Downloads/MySQL-5.6/libmysqlclient-dev_5.6.23-1ubuntu14.10_amd64.deb"
	if [[ ! -e "$path" ]]; then
		curl -LS "$url" |tee "$path" |md5sum >$curl_cache/mysql.have.md5
		# TODO: verify md5
	fi
	install -d tmp
	(
		cd tmp
		ar x "$path"
		tar -xJf data.tar.xz
		cp -al usr/include "$root/"
		cp -al usr/lib "$root/"
	)
	rm -rf ./tmp
fi

ldconfig || true
