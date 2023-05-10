FROM debian:11


RUN apt-get update
RUN apt-get install -y apt-utils
RUN apt-get install -y git shellcheck curl vim
RUN apt-get install -y \
    build-essential \
    libssl-dev \
    zlib1g-dev \
    libbz2-dev \
    libreadline-dev \
    libsqlite3-dev \
    libncurses5-dev \
    libncursesw5-dev \
    xz-utils \
    tk-dev \
    libffi-dev \
    liblzma-dev

ARG USERNAME
RUN : "${USERNAME:?Build argument not set.}"
ARG USERID
RUN : "${USERID:?Build argument not set.}"
ARG GROUPID
RUN : "${GROUPID:?Build argument not set.}"

RUN addgroup --gid "${GROUPID}" "${USERNAME}"
RUN useradd --uid "${USERID}" --gid "${GROUPID}" --home-dir "/home/${USERNAME}" -m "${USERNAME}"
#RUN adduser --system --no-create-home ${USERNAME}

USER ${USERNAME}

ENV HOME="/home/${USERNAME}"
ENV LANG="en_US.UTF-8"
ENV PATH="$HOME/.pyenv/bin:$HOME/.local/bin:$PATH"

RUN curl https://pyenv.run | bash

# We install one version of Python in order to have a way to bootstrap pipenv.
# This version doesn't _have_ to be the same as the one in Pipfile, but in case
# its being changed permanently there, the version below should be adapted to
# avoid an unnecessary extra Python version in the generated image.
RUN pyenv install 3.10.4
RUN pyenv global 3.10.4

RUN eval "$(pyenv init -)" \
    && python3 -m pip install --upgrade pip pipenv

ENTRYPOINT ["sh", "-c", "eval \"$(pyenv init -)\" && \"$@\"", "-s"]

