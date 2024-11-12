FROM artifacts.lan.tribe29.com:4000/python@sha256:7ec3bcfb507cb44387576100dd8eaf2d36756b0e4311238c031df3954c4dfbdd

# git is required to run pre-commit hooks
# call docker with "-u root" as the default user of the image is "root"
RUN apt-get update && apt-get install -y curl git shellcheck
RUN apt-get autoremove && apt-get clean

# Ensure all our build containers have the jenkins user (with same uid/gid). The non privileged
# jobs will be executed as this user in the container
RUN groupadd -g 1000 jenkins \
    && useradd -u 1001 -g jenkins --create-home --shell /bin/bash jenkins

USER jenkins

# install latest available poetry
RUN curl -sSL https://install.python-poetry.org | python -

# update the PATH variable with the poetry installation directory
ENV PATH="${PATH}:/home/jenkins/.local/bin"

# report installed poetry version, testing PATH
RUN poetry --version
