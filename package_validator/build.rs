// Copyright (C) 2026 Checkmk GmbH - License: GNU General Public License v2
// This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
// conditions defined in the file COPYING, which is part of this source code package.

//! Build script to generate test fixtures for the `package_validator` crate.
//!
//! This script attempts to generate all required test fixtures including:
//! - Simple non-ELF test files (always generated)
//! - ELF files with various RPATH/RUNPATH settings (requires gcc + patchelf)
//! - DEB packages (requires gcc + fakeroot + dpkg-deb)
//! - RPM packages (requires gcc + rpmbuild)
//!
//! If required tools are not available, the script will skip those fixtures
//! and emit warnings. Tests will gracefully skip when fixtures are missing.

use std::env;
use std::fs;
use std::path::Path;
use std::process::Command;

/// Check if a command is available in PATH.
fn command_exists(cmd: &str) -> bool {
    Command::new("which")
        .arg(cmd)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// Available tools for fixture generation.
#[allow(clippy::struct_excessive_bools)]
struct AvailableTools {
    gcc: bool,
    patchelf: bool,
    fakeroot: bool,
    dpkg_deb: bool,
    rpmbuild: bool,
    rpmdb: bool,
}

impl AvailableTools {
    fn detect() -> Self {
        Self {
            gcc: command_exists("gcc"),
            patchelf: command_exists("patchelf"),
            fakeroot: command_exists("fakeroot"),
            dpkg_deb: command_exists("dpkg-deb"),
            rpmbuild: command_exists("rpmbuild"),
            rpmdb: command_exists("rpmdb"),
        }
    }

    fn can_patch_elf(&self) -> bool {
        self.gcc && self.patchelf
    }

    fn can_build_deb(&self) -> bool {
        self.gcc && self.fakeroot && self.dpkg_deb
    }

    fn can_build_rpm(&self) -> bool {
        self.gcc && self.rpmbuild && self.rpmdb
    }

    fn report_missing(&self) {
        let mut missing = Vec::new();
        if !self.gcc {
            missing.push("gcc");
        }
        if !self.patchelf {
            missing.push("patchelf");
        }
        if !self.fakeroot {
            missing.push("fakeroot");
        }
        if !self.dpkg_deb {
            missing.push("dpkg-deb");
        }
        if !self.rpmbuild {
            missing.push("rpmbuild");
        }
        if !self.rpmdb {
            missing.push("rpmdb");
        }

        if !missing.is_empty() {
            println!(
                "cargo:warning=Some fixture generation tools are missing: {}. Some test fixtures will not be generated.",
                missing.join(", ")
            );
        }
    }
}

fn main() {
    let manifest_dir = env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR not set");
    let examples_dir = Path::new(&manifest_dir).join("examples");

    // Create examples directory if it doesn't exist
    fs::create_dir_all(&examples_dir).expect("Failed to create examples directory");

    // Detect available tools
    let tools = AvailableTools::detect();
    tools.report_missing();

    // Generate simple test fixtures (no external tools required)
    generate_simple_fixtures(&examples_dir);

    // Generate ELF test files (requires gcc + patchelf)
    if tools.can_patch_elf() {
        generate_elf_fixtures(&examples_dir);
    }

    // Generate DEB package (requires gcc + fakeroot + dpkg-deb)
    if tools.can_build_deb() {
        generate_deb_package(&examples_dir, "test");
    }

    // Generate RPM package (requires gcc + rpmbuild)
    if tools.can_build_rpm() {
        generate_rpm_package(&examples_dir, "test");
    }

    // Re-run build script if examples directory changes
    println!("cargo:rerun-if-changed=examples/");
}

/// Generate simple test fixtures that don't require external tools.
fn generate_simple_fixtures(examples_dir: &Path) {
    // File too small to be an ELF (< 64 bytes)
    let too_small_path = examples_dir.join("test-elf-file-too-small");
    if !too_small_path.exists() {
        fs::write(&too_small_path, "not an elf file")
            .expect("Failed to write test-elf-file-too-small");
    }

    // File that's large enough but not an ELF (wrong magic bytes)
    let not_elf_path = examples_dir.join("test-elf-not-elf-file");
    if !not_elf_path.exists() {
        let content = "This is not an ELF file. It's just a text file for testing. \
                       Adding more content to ensure it's longer than 64 bytes which is \
                       the minimum size for a valid ELF file header.";
        fs::write(&not_elf_path, content).expect("Failed to write test-elf-not-elf-file");
    }
}

/// Generate ELF test files with various RPATH/RUNPATH settings.
fn generate_elf_fixtures(examples_dir: &Path) {
    let temp_dir = env::temp_dir().join("package_validator_build");
    let _ = fs::remove_dir_all(&temp_dir);
    fs::create_dir_all(&temp_dir).expect("Failed to create temp directory");

    // Create and compile a simple test binary
    let source_path = temp_dir.join("test_binary.c");
    let binary_path = temp_dir.join("test_binary");

    let source_code = r#"#include <stdio.h>
int main() {
    printf("Test binary\n");
    return 0;
}
"#;

    fs::write(&source_path, source_code).expect("Failed to write test source");

    // Compile the base binary
    let compile_status = Command::new("gcc")
        .args([
            "-o",
            binary_path.to_str().unwrap(),
            source_path.to_str().unwrap(),
            "-Wl,--disable-new-dtags",
        ])
        .status();

    if compile_status.map(|s| !s.success()).unwrap_or(true) {
        println!("cargo:warning=Failed to compile test binary, skipping ELF fixture generation");
        let _ = fs::remove_dir_all(&temp_dir);
        return;
    }

    // Generate ELF files with various RPATH settings
    let elf_fixtures = [
        (
            "test-elf-valid-absolute-rpath.elf",
            "/usr/lib",
            true, // use --force-rpath (RPATH)
        ),
        ("test-elf-valid-origin-rpath.elf", "$ORIGIN/../lib", true),
        (
            "test-elf-valid-origin-braces-rpath.elf",
            "${ORIGIN}/lib",
            true,
        ),
        (
            "test-elf-valid-runpath.elf",
            "/opt/lib",
            false, // use RUNPATH (default)
        ),
        ("test-elf-invalid-relative-rpath.elf", "../lib", true),
        ("test-elf-invalid-relative-dot-rpath.elf", "./lib", true),
        (
            "test-elf-invalid-prefix-origin-rpath.elf",
            "../$ORIGIN/lib",
            true,
        ),
    ];

    for (filename, rpath_value, force_rpath) in elf_fixtures {
        let dest_path = examples_dir.join(filename);
        if dest_path.exists() {
            continue; // Skip if already exists
        }

        // Copy the base binary
        if fs::copy(&binary_path, &dest_path).is_err() {
            println!("cargo:warning=Failed to copy binary for {filename}");
            continue;
        }

        // Set RPATH/RUNPATH using patchelf
        let mut cmd = Command::new("patchelf");
        if force_rpath {
            cmd.arg("--force-rpath");
        }
        cmd.args(["--set-rpath", rpath_value, dest_path.to_str().unwrap()]);

        if cmd.status().map(|s| !s.success()).unwrap_or(true) {
            println!("cargo:warning=Failed to set RPATH for {filename}, removing file");
            let _ = fs::remove_file(&dest_path);
        }
    }

    // Cleanup temp directory
    let _ = fs::remove_dir_all(&temp_dir);
}

/// Create C source files for package binaries.
fn create_c_sources(build_dir: &Path) {
    let lib_source = r#"#include <stdio.h>

void hello_from_lib() {
    printf("Hello from shared library!\n");
}
"#;

    let bin_source = r#"#include <stdio.h>

void hello_from_lib();

int main() {
    printf("Hello from binary!\n");
    hello_from_lib();
    return 0;
}
"#;

    fs::write(build_dir.join("libhello.c"), lib_source).expect("Failed to write libhello.c");
    fs::write(build_dir.join("hello.c"), bin_source).expect("Failed to write hello.c");
}

/// Build binaries for packages.
fn build_package_binaries(build_dir: &Path) -> bool {
    // Build shared library
    let lib_status = Command::new("gcc")
        .args([
            "-shared",
            "-fPIC",
            "-o",
            build_dir.join("libhello.so").to_str().unwrap(),
            build_dir.join("libhello.c").to_str().unwrap(),
            "-Wl,--disable-new-dtags",
        ])
        .status();

    if lib_status.map(|s| !s.success()).unwrap_or(true) {
        return false;
    }

    // Build binary with RPATH
    let bin_status = Command::new("gcc")
        .args([
            "-o",
            build_dir.join("hello").to_str().unwrap(),
            build_dir.join("hello.c").to_str().unwrap(),
            &format!("-L{}", build_dir.display()),
            "-lhello",
            "-Wl,-rpath,$ORIGIN/../lib",
        ])
        .status();

    bin_status.map(|s| s.success()).unwrap_or(false)
}

/// Generate a DEB package.
fn generate_deb_package(examples_dir: &Path, package_name: &str) {
    let deb_file = examples_dir.join(format!("{package_name}.deb"));
    if deb_file.exists() {
        return; // Skip if already exists
    }

    let temp_dir = env::temp_dir().join(format!("package_validator_deb_{package_name}"));
    let _ = fs::remove_dir_all(&temp_dir);
    fs::create_dir_all(&temp_dir).expect("Failed to create temp directory");

    let package_dir = temp_dir.join(format!("deb_{package_name}"));
    let debian_dir = package_dir.join("DEBIAN");
    let bin_dir = package_dir.join("usr/bin");
    let lib_dir = package_dir.join("usr/lib");
    let build_dir = temp_dir.join("build");

    fs::create_dir_all(&debian_dir).expect("Failed to create DEBIAN directory");
    fs::create_dir_all(&bin_dir).expect("Failed to create bin directory");
    fs::create_dir_all(&lib_dir).expect("Failed to create lib directory");
    fs::create_dir_all(&build_dir).expect("Failed to create build directory");

    // Create control file
    let control_content = format!(
        "Package: {package_name}
Version: 1.0.0
Section: test
Priority: optional
Architecture: amd64
Maintainer: Test <test@example.com>
Description: Test package for RPATH validation
"
    );
    fs::write(debian_dir.join("control"), control_content).expect("Failed to write control file");

    // Build binaries
    create_c_sources(&build_dir);
    if !build_package_binaries(&build_dir) {
        println!("cargo:warning=Failed to build binaries for DEB package");
        let _ = fs::remove_dir_all(&temp_dir);
        return;
    }

    // Copy binaries to package directory
    fs::copy(build_dir.join("hello"), bin_dir.join("hello")).expect("Failed to copy hello binary");
    fs::copy(build_dir.join("libhello.so"), lib_dir.join("libhello.so"))
        .expect("Failed to copy libhello.so");

    // Build DEB package using fakeroot + dpkg-deb
    let status = Command::new("fakeroot")
        .args([
            "dpkg-deb",
            "--build",
            package_dir.to_str().unwrap(),
            deb_file.to_str().unwrap(),
        ])
        .status();

    if status.map(|s| !s.success()).unwrap_or(true) {
        println!("cargo:warning=Failed to build DEB package");
    }

    // Cleanup
    let _ = fs::remove_dir_all(&temp_dir);
}

/// Generate an RPM package.
fn generate_rpm_package(examples_dir: &Path, package_name: &str) {
    let rpm_file = examples_dir.join(format!("{package_name}.rpm"));
    if rpm_file.exists() {
        return; // Skip if already exists
    }

    let temp_dir = env::temp_dir().join(format!("package_validator_rpm_{package_name}"));
    let _ = fs::remove_dir_all(&temp_dir);
    fs::create_dir_all(&temp_dir).expect("Failed to create temp directory");

    let rpmbuild_dir = temp_dir.join("rpmbuild");
    let rpmdb_dir = temp_dir.join("rpmdb");
    let spec_dir = rpmbuild_dir.join("SPECS");
    let buildroot_dir = rpmbuild_dir.join("BUILDROOT");
    let package_buildroot = buildroot_dir.join(format!("{package_name}-1.0.0-1.x86_64"));
    let build_dir = temp_dir.join("build");

    fs::create_dir_all(&spec_dir).expect("Failed to create SPECS directory");
    fs::create_dir_all(package_buildroot.join("usr/bin")).expect("Failed to create bin directory");
    fs::create_dir_all(package_buildroot.join("usr/lib")).expect("Failed to create lib directory");
    fs::create_dir_all(&rpmdb_dir).expect("Failed to create rpmdb directory");
    fs::create_dir_all(&build_dir).expect("Failed to create build directory");

    // Initialize local RPM database
    let _ = Command::new("rpmdb")
        .args(["--initdb", "--dbpath", rpmdb_dir.to_str().unwrap()])
        .status();

    // Create spec file
    let spec_content = format!(
        "Name:           {package_name}
Version:        1.0.0
Release:        1
Summary:        Test package for RPATH validation
License:        MIT
BuildArch:      x86_64

%description
Test package for RPATH validation

%files
/usr/bin/hello
/usr/lib/libhello.so
"
    );
    let spec_path = spec_dir.join(format!("{package_name}.spec"));
    fs::write(&spec_path, spec_content).expect("Failed to write spec file");

    // Build binaries
    create_c_sources(&build_dir);
    if !build_package_binaries(&build_dir) {
        println!("cargo:warning=Failed to build binaries for RPM package");
        let _ = fs::remove_dir_all(&temp_dir);
        return;
    }

    // Copy binaries to buildroot
    fs::copy(
        build_dir.join("hello"),
        package_buildroot.join("usr/bin/hello"),
    )
    .expect("Failed to copy hello binary");
    fs::copy(
        build_dir.join("libhello.so"),
        package_buildroot.join("usr/lib/libhello.so"),
    )
    .expect("Failed to copy libhello.so");

    // Build RPM package
    let status = Command::new("rpmbuild")
        .args([
            "--dbpath",
            rpmdb_dir.to_str().unwrap(),
            "--define",
            &format!("_topdir {}", rpmbuild_dir.display()),
            "--define",
            &format!("_builddir {}/BUILD", rpmbuild_dir.display()),
            "--define",
            &format!("_rpmdir {}/RPMS", rpmbuild_dir.display()),
            "--define",
            &format!("_sourcedir {}/SOURCES", rpmbuild_dir.display()),
            "--define",
            &format!("_specdir {}", spec_dir.display()),
            "--define",
            &format!("_srcrpmdir {}/SRPMS", rpmbuild_dir.display()),
            "--buildroot",
            package_buildroot.to_str().unwrap(),
            "-bb",
            spec_path.to_str().unwrap(),
        ])
        .output();

    match status {
        Ok(output) if output.status.success() => {
            // Move RPM to examples directory
            let rpm_output =
                rpmbuild_dir.join(format!("RPMS/x86_64/{package_name}-1.0.0-1.x86_64.rpm"));
            if let Err(e) = fs::copy(&rpm_output, &rpm_file) {
                println!("cargo:warning=Failed to copy RPM to examples: {e}");
            }
        }
        Ok(output) => {
            let stderr = String::from_utf8_lossy(&output.stderr);
            // Filter common non-error messages
            if !stderr.contains("Unable to open sqlite database")
                && !stderr.contains("cannot open Packages database")
            {
                println!("cargo:warning=rpmbuild failed: {stderr}");
            }
        }
        Err(e) => {
            println!("cargo:warning=Failed to run rpmbuild: {e}");
        }
    }

    // Cleanup
    let _ = fs::remove_dir_all(&temp_dir);
}
