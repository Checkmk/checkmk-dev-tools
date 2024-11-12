#!groovy

/// file: jenkins_job_entry.groovy

/// This is the checkmk_ci specific main entry point. It exists to
/// avoid redundant code in the actual job definition files and to be able
/// to provide a standard environment for all Checkmk jobs

def main(job_definition_file) {
    load("${checkout_dir}/${job_definition_file}").main();
}

return this;
