{
  description = "A very basic flake";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    fenix = {
      url = "github:nix-community/fenix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    naersk = {
      url = "github:nix-community/naersk";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      fenix,
      naersk,
    }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};

      # Flake options
      enableBPF = true;
      enableRust = true;

      buildLib = pkgs.callPackage ./build { };

      linuxConfigs = pkgs.callPackage ./configs/kernel.nix {
        inherit
          enableBPF
          enableRust
          ;
      };
      inherit (linuxConfigs) kernelArgs kernelConfig;

      configModule = buildLib.buildKernelConfigModule {
        inherit (kernelConfig)
          structuredExtraConfig
          ;
        inherit nixpkgs;
      };

      # Config file derivation
      configfile = buildLib.buildKernelConfig {
        inherit (kernelConfig)
          generateConfigFlags
          ;
        inherit configModule kernel nixpkgs;
      };

      # Kernel derivation.
      kernelDrv = buildLib.buildKernel {
        inherit (kernelArgs)
          src
          modDirVersion
          version
          kernelPatches
          ;

        inherit configModule configfile nixpkgs;
      };

      linuxDev = pkgs.linuxPackagesFor kernelDrv;
      kernel = linuxDev.kernel;

      buildRustModule = buildLib.buildRustModule { inherit kernel; };
      buildCModule = buildLib.buildCModule {
        inherit kernel;
      };

      modules = [ cModule ] ++ pkgs.lib.optional enableRust rustModule;

      initramfs = buildLib.buildInitramfs {
        inherit kernel modules;

        extraBin = {
          strace = "${pkgs.strace}/bin/strace";
        }
        // pkgs.lib.optionalAttrs enableBPF {
          stackcount = "${pkgs.bcc}/bin/stackcount";
        };
        storePaths = [
          pkgs.foot.terminfo
        ]
        ++ pkgs.lib.optionals enableBPF [
          pkgs.bcc
          pkgs.python3
        ];
      };

      runQemu = buildLib.buildQemuCmd { inherit kernel initramfs; };
      runGdb = buildLib.buildGdbCmd { inherit kernel modules; };

      cModule = buildCModule {
        name = "helloworld";
        src = ./modules/helloworld;
      };

      rustModule = buildRustModule {
        name = "rust-out-of-tree";
        src = ./modules/rust;
      };

      ebpf-stacktrace = pkgs.stdenv.mkDerivation {
        name = "ebpf-stacktrace";
        src = ./ebpf/ebpf_stacktrace;
        installPhase = ''
          runHook preInstall

          mkdir $out
          cp ./helloworld $out/
          cp ./helloworld_dbg $out/
          cp runit.sh $out/

          runHook postInstall
        '';
        meta.platforms = [ "x86_64-linux" ];
      };

      genRustAnalyzer = pkgs.writers.writePython3Bin "generate-rust-analyzer" { } (
        builtins.readFile ./scripts/generate_rust_analyzer.py
      );

      avy-init = pkgs.callPackage ./avy-init {
        inherit fenix naersk;
      };

      devShell =
        let
          nativeBuildInputs =
            with pkgs;
            [
              bear # for compile_commands.json, use bear -- make
              runQemu
              runGdb
              git
              gdb
              qemu
              pahole

              # static analysis
              flawfinder
              cppcheck
              sparse
              rustc

            ]
            ++ lib.optionals enableRust [
              cargo
              rustfmt
              genRustAnalyzer
            ];
          buildInputs = [
            pkgs.nukeReferences
            kernel.dev
          ];
        in
        pkgs.mkShell {
          inherit buildInputs nativeBuildInputs;
          KERNEL = kernel.dev;
          KERNEL_VERSION = kernel.modDirVersion;
          RUST_LIB_SRC = pkgs.rustPlatform.rustLibSrc;
        };
    in
    {
      lib = {
        builders = import ./build/default.nix;
      };

      packages.${system} = {
        inherit
          initramfs
          kernelDrv
          kernel
          cModule
          ebpf-stacktrace
          rustModule
          genRustAnalyzer
          avy-init
          ;
        kernelConfig = configfile;
      };

      devShells.${system}.default = devShell;

      formatter.${system} = pkgs.nixfmt;
    };
}
