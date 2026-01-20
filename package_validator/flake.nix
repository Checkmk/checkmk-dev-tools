{
  description = "Package validator for deb/rpm packages";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, rust-overlay }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        overlays = [ (import rust-overlay) ];
        pkgs = import nixpkgs {
          inherit system overlays;
        };
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            (rust-bin.stable.latest.default.override {
              extensions = [ "rust-src" "rust-analyzer" ];
            })
            # Needed for package extraction.
            cpio
            dpkg
            rpm
            # Development tools
            cargo-make
            # Needed for generating example packages
            gcc
            fakeroot
            patchelf
          ];

          shellHook = ''
            echo "Development environment ready!"
            echo "Rust version: $(rustc --version)"
            echo "Cargo version: $(cargo --version)"
            echo "Rust-analyzer version: $(rust-analyzer --version)"
          '';
        };
      }
    );
}

