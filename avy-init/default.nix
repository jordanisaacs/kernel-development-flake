{ pkgs, fenix, naersk }:

let
  # Use the musl cross-compilation target
  crossPkgs = pkgs.pkgsCross.musl64;

  # Create a Rust toolchain for the target
  rustTarget = "x86_64-unknown-linux-musl";

  # Get the toolchain with the musl target included using fenix
  toolchain = fenix.packages.${pkgs.system}.combine [
    fenix.packages.${pkgs.system}.stable.rustc
    fenix.packages.${pkgs.system}.stable.cargo
    fenix.packages.${pkgs.system}.targets.${rustTarget}.stable.rust-std
  ];

  # Create naersk instance with our custom toolchain
  naersk-lib = naersk.lib.${pkgs.system}.override {
    cargo = toolchain;
    rustc = toolchain;
  };
in
naersk-lib.buildPackage {
  pname = "avy-init";
  version = "0.1.0";

  src = ./.;

  strictDeps = true;
  doCheck = false;

  nativeBuildInputs = [ crossPkgs.stdenv.cc ];

  CARGO_BUILD_TARGET = rustTarget;
  TARGET_CC = "${crossPkgs.stdenv.cc}/bin/${crossPkgs.stdenv.cc.targetPrefix}cc";

  CARGO_BUILD_RUSTFLAGS = [
    "-C" "target-feature=+crt-static"
    "-C" "link-arg=-static"
  ];

  meta = with pkgs.lib; {
    description = "A minimal Rust init program for initramfs with virtiofs and overlayfs support";
    platforms = platforms.linux;
  };
}
