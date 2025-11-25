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
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:adisbladis/uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      fenix,
      naersk,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
    }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system}.extend fenix.overlays.default;

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

      kdf-init = pkgs.callPackage ./kdf-init {
        inherit fenix naersk;
      };

      kdf-cli = pkgs.callPackage ./kdf-cli {
        inherit uv2nix pyproject-nix pyproject-build-systems;
      };

      devShell =
        let
          # Rust toolchain with musl target for static compilation
          rustToolchain = pkgs.fenix.combine [
            pkgs.fenix.stable.rustc
            pkgs.fenix.stable.cargo
            pkgs.fenix.stable.rustfmt
            pkgs.fenix.targets.x86_64-unknown-linux-musl.stable.rust-std
          ];

          nativeBuildInputs =
            with pkgs;
            [
              bear # for compile_commands.json, use bear -- make
              # runQemu (TODO: replace with kdf-cli)
              # runGdb (TODO: replace with kdf-cli)
              git
              gdb
              qemu
              pahole

              # static analysis
              flawfinder
              cppcheck
              sparse
            ]
            ++ lib.optionals enableRust [
              rustToolchain
              # genRustAnalyzer
            ];
          buildInputs = [
            pkgs.nukeReferences
            # kernel.dev
          ];
        in
        pkgs.mkShell {
          inherit buildInputs nativeBuildInputs;
          # KERNEL = kernel.dev;
          # KERNEL_VERSION = kernel.modDirVersion;
          # RUST_LIB_SRC = pkgs.rustPlatform.rustLibSrc;
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
          kdf-init
          kdf-cli
          ;
        kernelConfig = configfile;
      };

      devShells.${system}.default = devShell;

      formatter.${system} = pkgs.nixfmt;
    };
}
