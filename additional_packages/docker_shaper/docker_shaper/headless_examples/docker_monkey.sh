#!/usr/bin/env bash

set -e

cd "$(dirname "$(realpath "$0")")" || exit 1

#docker pull ubuntu:22.04

#sleep 1

docker build -t monkey-image --no-cache .

#sleep 1

docker run --name monkey-container -it monkey-image sh -c "echo hallo; sleep 1"

#sleep 1

docker rm monkey-container

#sleep 1

docker image rm monkey-image

#sleep 1

docker image rm ubuntu:22.04

