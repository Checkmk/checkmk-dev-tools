properties([
    buildDiscarder(logRotator(
        artifactDaysToKeepStr: '',
        artifactNumToKeepStr: '',
        daysToKeepStr: '7',
        numToKeepStr: '200')),
])

def NODE = '';

withFolderProperties {
    NODE = env.BUILD_NODE;
}

timeout(time: 12, unit: 'HOURS') {
    node(NODE) {
        do_it();
    }
}

def DEV_IMAGE;

def do_it() {
    stage("check out") {
        checkout(scm);
    }
    stage("build dev container") {
        DEV_IMAGE = docker.build("checkmk_dev_tools", "-f docker/Dockerfile .");
        run_target("make dist;pip3 install checkmk_dev_tools-0.0.1.tar.gz");
    }
    stage("lint python: bandit") {
        run_target("make lint-python/bandit");
    }
    stage("lint python: format") {
        run_target("make lint-python/format");
    }
    stage("lint python: pylint") {
        run_target("make lint-python/pylint");
    }
    stage("typing python: mypy") {
        run_target("make typing-python/mypy");
    }
    stage("python unit and doc test") {
        run_target("make test-unit");
    }
}

def run_target(target) {
    DEV_IMAGE.inside("--entrypoint=") {
        sh("#!/bin/ash\n${target}");
    }
}
