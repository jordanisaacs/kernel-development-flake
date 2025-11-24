{
  stdenv,
  lib,
  callPackage,
  bison,
  flex,
  perl,
  bc,
  openssl,
  rsync,
  gmp,
  libmpc,
  mpfr,
  elfutils,
  zstd,
  python3Minimal,
  kmod,
  hexdump,
  cpio,
  pahole,
  zlib,
  rustc-unwrapped,
  rust-bindgen-unwrapped,
  rustPlatform,
}:
{
  src,
  configfile,
  # Up to caller to ensure that 'configfile' was generated from this
  # 'configModule'.
  #
  # This is a hack. It is accessible as passthru in 'configfile' but it results
  # in infinite recursion. So instead we pass in 'configModule' separate from
  # 'configFile'.
  configModule,
  modDirVersion,
  version,
  kernelPatches ? [ ],
  nixpkgs, # Nixpkgs source
}:
let
  config = configModule.structuredConfig;

  # Using custom checkers that work with the config module.
  #
  configAcc = rec {
    getValue = attr: config.${attr}.tristate or null;

    isYes = attr: getValue attr == "y";
  };
  withRust = configAcc.isYes "RUST";
  kernel =
    ((callPackage "${nixpkgs}/pkgs/os-specific/linux/kernel/build.nix" { }) {
      inherit
        src
        modDirVersion
        version
        kernelPatches
        configfile
        ;
      inherit lib stdenv config;
    }).overrideAttrs
      (old: {
        dontStrip = true;

        # We always install modules
        outputs = [
          "out"
          "dev"
          "modules"
        ];

        buildFlags = [
          "KBUILD_BUILD_VERSION=1-NixOS"
          stdenv.hostPlatform.linux-kernel.target
          "vmlinux" # for "perf" and things like that
          "modules"
          "scripts_gdb"
        ]
        ++ lib.optional withRust "rust-analyzer";

        installFlags = [
          "INSTALL_PATH=${placeholder "out"}"
          "INSTALL_MOD_PATH=${placeholder "modules"}"
        ];

        nativeBuildInputs = [
          bison
          flex
          perl
          bc
          openssl
          rsync
          gmp
          libmpc
          mpfr
          elfutils
          zstd
          python3Minimal
          kmod
          hexdump
        ]
        ++ lib.optionals (lib.versionAtLeast version "5.2") [
          cpio
          pahole
          zlib
        ]
        ++ lib.optionals withRust [
          rustc-unwrapped
          rust-bindgen-unwrapped
        ];

        env = {
          RUST_LIB_SRC = lib.optionalString withRust rustPlatform.rustLibSrc;

          # avoid leaking Rust source file names into the final binary, which adds
          # a false dependency on rust-lib-src on targets with uncompressed kernels
          # UNDONE: I think we don't want this, comment for now.
          # KRUSTFLAGS = lib.optionalString withRust "--remap-path-prefix ${rustPlatform.rustLibSrc}=/";
        };

        postInstall = ''
          mkdir -p $dev
          cp vmlinux $dev/

          mkdir -p $dev/lib/modules/${modDirVersion}/{build,source}
          cp -rL $buildRoot/scripts $dev/lib/modules/${modDirVersion}/build/
          cp -L $buildRoot/vmlinux-gdb.py $dev/lib/modules/${modDirVersion}/build/scripts/gdb/
          ln -sfn $dev/lib/modules/${modDirVersion}/build/scripts/gdb/vmlinux-gdb.py $dev/lib/modules/${modDirVersion}/build/vmlinux-gdb.py

          if [ -z "''${dontStrip-}" ]; then
            installFlags+=("INSTALL_MOD_STRIP=1")
          fi
          make modules_install "''${makeFlags[@]}" "''${installFlags[@]}"
          unlink $modules/lib/modules/${modDirVersion}/build

          # To save space, exclude a bunch of unneeded stuff when copying.
          (cd .. && rsync --archive --prune-empty-dirs \
              --exclude='/build/' \
              * $dev/lib/modules/${modDirVersion}/source/)

          cd $dev/lib/modules/${modDirVersion}/source
          cp $buildRoot/{.config,Module.symvers} $dev/lib/modules/${modDirVersion}/build

          make modules_prepare "''${makeFlags[@]}" O=$dev/lib/modules/${modDirVersion}/build

          # For reproducibility, removes accidental leftovers from a `cc1` call
          # from a `try-run` call from the Makefile
          rm -f $dev/lib/modules/${modDirVersion}/build/.[0-9]*.d

          # Keep some extra files on some arches (powerpc, aarch64)
          for f in arch/powerpc/lib/crtsavres.o arch/arm64/kernel/ftrace-mod.o; do
            if [ -f "$buildRoot/$f" ]; then
              cp $buildRoot/$f $dev/lib/modules/${modDirVersion}/build/$f
            fi
          done

          # Not doing the nix default of removing files from the source tree.
          # This is because the source tree is necessary for debugging with GDB.
        '';
      });

  kernelPassthru = {
    inherit (configfile) structuredConfig;
    inherit modDirVersion configfile;
    passthru = kernel.passthru // (removeAttrs kernelPassthru [ "passthru" ]);
  };
in
lib.extendDerivation true kernelPassthru kernel
