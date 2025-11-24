{
  stdenv,
  lib,
  perl,
  gmp,
  libmpc,
  mpfr,
  bison,
  flex,
  pahole,
  buildPackages,
  rustPlatform,
  rust-bindgen-unwrapped,
  rustc-unwrapped,
}:
{
  nixpkgs,
  kernel,
  # generate-config.pl flags. see below
  generateConfigFlags,
  configModule,
}:
let
  withRust = ((configModule.structuredConfig.RUST or { }).tristate or null) == "y";
in
stdenv.mkDerivation {
  kernelArch = stdenv.hostPlatform.linuxArch;
  extraMakeFlags = [ ];

  inherit (kernel) src patches version;
  pname = "linux-config";

  # Flags that get passed to generate-config.pl
  # ignoreConfigErrors: Ignores any config errors in script (eg unused options)
  # autoModules: Build every available module
  # preferBuiltin: Build modules as builtin
  inherit (generateConfigFlags) autoModules preferBuiltin ignoreConfigErrors;
  generateConfig = "${nixpkgs}/pkgs/os-specific/linux/kernel/generate-config.pl";

  kernelConfig = configModule.moduleStructuredConfig.intermediateNixConfig;
  passAsFile = [ "kernelConfig" ];

  depsBuildBuild = [ buildPackages.stdenv.cc ];
  nativeBuildInputs = [
    perl
    gmp
    libmpc
    mpfr
    bison
    flex
    pahole
  ]
  ++ lib.optionals withRust [
    rust-bindgen-unwrapped
    rustc-unwrapped
  ];

  RUST_LIB_SRC = lib.optionalString withRust rustPlatform.rustLibSrc;

  platformName = stdenv.hostPlatform.linux-kernel.name;
  # e.g. "bzImage"
  kernelTarget = stdenv.hostPlatform.linux-kernel.target;

  makeFlags = lib.optionals (
    stdenv.hostPlatform.linux-kernel ? makeFlags
  ) stdenv.hostPlatform.linux-kernel.makeFlags;

  postPatch = kernel.postPatch + ''
    # Patch kconfig to print "###" after every question so that
    # generate-config.pl from the generic builder can answer them.
    sed -e '/fflush(stdout);/i\printf("###");' -i scripts/kconfig/conf.c
  '';

  preUnpack = kernel.preUnpack or "";

  buildPhase = ''
    export buildRoot="''${buildRoot:-build}"
    export HOSTCC=$CC_FOR_BUILD
    export HOSTCXX=$CXX_FOR_BUILD
    export HOSTAR=$AR_FOR_BUILD
    export HOSTLD=$LD_FOR_BUILD

    # Get a basic config file for later refinement with $generateConfig.
    make $makeFlags \
      -C . O="$buildRoot" allnoconfig \
      ARCH=$kernelArch CROSS_COMPILE=${stdenv.cc.targetPrefix} \
      $makeFlags

    # Create the config file.
    echo "generating kernel configuration..."
    ln -s "$kernelConfigPath" "$buildRoot/kernel-config"
    DEBUG=1 ARCH=$kernelArch CROSS_COMPILE=${stdenv.cc.targetPrefix} \
      KERNEL_CONFIG="$buildRoot/kernel-config" AUTO_MODULES=$autoModules \
      PREFER_BUILTIN=$preferBuiltin BUILD_ROOT="$buildRoot" SRC=. MAKE_FLAGS="$makeFlags" \
      perl -w $generateConfig
  ''
  + lib.optionalString stdenv.cc.isClang ''
        if ! grep -Fq CONFIG_CC_IS_CLANG=y $buildRoot/.config; then
      echo "Kernel config didn't recognize the clang compiler?"
      exit 1
    fi
  ''
  + lib.optionalString stdenv.cc.bintools.isLLVM ''
    if ! grep -Fq CONFIG_LD_IS_LLD=y $buildRoot/.config; then
      echo "Kernel config didn't recognize the LLVM linker?"
      exit 1
    fi
  ''
  + lib.optionalString withRust ''
    if ! grep -Fq CONFIG_RUST_IS_AVAILABLE=y $buildRoot/.config; then
      echo "Kernel config didn't find Rust toolchain?"
      exit 1
    fi
  '';

  installPhase = "mv $buildRoot/.config $out";

  enableParallelBuilding = true;

  passthru = configModule;
}
