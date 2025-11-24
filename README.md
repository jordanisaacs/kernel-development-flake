# Kernel Module Flake

A nix flake dedicated to making the developer tooling around kernel module development easier. There are two ways to use this flake:

1. Clone this flake and start hacking.
2. Add this flake as an input to your own flake and use the provided scripts and builder functions.

The first way is recommended if you just want to get up and running immediately and start hacking. The second way is for someone who wants to integrate this into their own kernel module development process without keeping a thousand lines of Nix up to date.

## Features

* Compile a minimal kernel designed for debugging (Time to build on Ryzen 5 3600 - 6 cores):

![time](https://user-images.githubusercontent.com/19742638/201808063-3315027f-44c6-4bd7-bf48-a835b1ffe096.png)

* Enabled by default Rust support & the ability to switch to the Rust-For-Linux branch
* Nix functions for building Rust and C kernel modules
* QEMU VM support using Nix's built in functions for generating an initramfs
* Remote GDB debugging through the VM
* Out of tree rust-analyzer support

## Cloning the flake

Get started by cloning this repository.

```bash
git clone git@github.com:jordanisaacs/kernel-module-flake
cd kernel-module-flake

# nix develop .# or direnv allow to get into the dev environment

runvm   # Calls QEMU with the necessary commands, uses sudo for enabling kvm

#### Inside QEMU
# insmod module/helloworld.ko   # Load the kernel module
# rmmod module/helloworld.ko    # Unload the module
#### C^A+X to exit
#### In another terminal while the VM is running
# rungdb                        # Connect to the VM with remote GDB debugging
### (GDB)
## lx-symbols-nix               # Runs lx-symbols with the nix store paths of the modules
####


cd helloworld
bear -- make            # generate the compile_commands.json
vim helloworld.c        # Start editing!

# exit and then nix develop .# or just direnv reload
# to rebuild and update the runvm command
```

## Flake as an input

The `lib.builders` output of the flake exposes all the components as Nix builder functions. You can use them to compile your own kernel, configfile, initramfs, and generate the `runvm` and `rungdb` commands. An example of how the functions are used is below. See the `flake.nix` file for more details, and the `build` directory for the arguments that can be passed to the builders.

```nix
{
   inputs.kernelFlake.url = "github:jordanisaacs/kernel-module-flake";

   outputs =  {
     self,
     nixpkgs,
     kernelFlake
   }: let
     system = "x86_64-system";
     pkgs = nixpkgs.legacyPackages.${system};

     kernelLib = kernelFlake.lib.builders {inherit pkgs;};

     buildRustModule = buildLib.buildRustModule {inherit kernel;};
     buildCModule = buildLib.buildCModule {inherit kernel;};

     configfile = buildLib.buildKernelConfig {
       generateConfigFlags = {};
       structuredExtraConfig = {};

       inherit kernel nixpkgs;
     };

     kernel = buildLib.buildKernel {
       inherit configfile;

       src = ./kernel-src;
       version = "";
       modDirVersion = "";
     };

     modules = [exampleModule];

     initramfs = buildLib.buildInitramfs {
       inherit kernel modules;
     };

     exampleModule = buildCModule { name = "example-module"; src = ./.; };

     runQemu = buildLib.buildQemuCmd {inherit kernel initramfs;};
     runGdb = buildLib.buildGdbCmd {inherit kernel modules;};
   in { };
}
```

## How it works

### Kernel Build

A custom kernel is built according to Chris Done's [Build and run minimal Linux / Busybox systems in Qemu](https://gist.github.com/chrisdone/02e165a0004be33734ac2334f215380e). Extra config is added which I got through Kaiwan Billimoria's [Linux Kernel Programming](https://www.packtpub.com/product/linux-kernel-programming/9781789953435).

First a derivation is built for the `.config` file.  It is generated using a modified version of the `configfile` derivation in the [generic kernel builder](https://github.com/NixOS/nixpkgs/blob/nixos-unstable/pkgs/os-specific/linux/kernel/generic.nix) (also known as the `buildLinux` function). This modified derivation is required to remove the NixOS distribution default configuration. More documentation is in the `build/c-module.nix` the flake.

Compiling the kernel is the same as `Nix` but modified to not remove any of the source files from the dev output. This is because they are necessary for things such as gdb debugging, and rust development.

Then a new package set called `linuxDev` is then added as an overlay using `linuxPackagesFor`.

### Rust Support

Rust support is enabled by using the default configuration with `enableRust = true` or setting `RUST = true` in the kernel configuration. The build will automatically pick up up the value set in the kernel config and build correctly.

### Kernel Modules

The kernel modules are built using nix. You can build them manually with `nix build .#helloworld` and `nix build .#rust`. They are copied into the initramfs for you. There is a `buildCModule` and `buildRustModule` function exposed for building your own modules (`build/rust-module.nix` and `build/c-module.nix`).

### eBPF Support

eBPF is enabled by default. This makes the initrd much larger due to needing python for `bcc`, and the compile time of the linux kernel longer. You can disable it by setting `enableBPF = false` in `flake.nix`.

### Remote GDB

Remote GDB debugging is activated through the `rungdb` command (`build/run-gdb.nix`). It wraps GDB to provide the kernel source in the search path, loads `vmlinux`, sources the kernel gdb scripts, and then connects to the VM. An alias is provided `lx-symbols-nix` that runs the `lx-symbols` command with all the provided modules' nix store paths as search directories.

### initramfs

The initial ram disk is built using the new [make-initrd-ng](https://github.com/NixOS/nixpkgs/tree/master/pkgs/build-support/kernel/make-initrd-ng). It is called through its [nix wrapper](https://github.com/NixOS/nixpkgs/blob/master/pkgs/build-support/kernel/make-initrd-ng.nix) which safely copies the nix store packages needed over. To see how to include modules and other options see the builder, `build/initramfs.nix`.

### Editor

How to get language servers working.

#### C

Clang-format was copied over from the linux source tree. To get CCLS working correctly call `bear -- make` to get a `compile_commands.json`. Then open up C files in your favorite editor with an LSP set up.

#### Rust

The flake is configured to build the kernel with a `rust-project.json` but it is not usable to out of tree modules. A script is run that parses the kernel's `rust-project.json` and generates one for the module itself. It is accessed with `make rust-analyzer`. Credit to thepacketgeek for the [script](https://github.com/Rust-for-Linux/rust-out-of-tree-module/pull/2). Additionally, rust-analyzer is designed to use `cargo check` for diagnostics. There is an opt-out to use rustc outputs which is configured within the editor's rust-analyzer configuration.

The rust-analyzer options should be look something along the lines of:

```nix
let
  cmd =
    writeShellScript
    "module-ra-check"
    ''make -s "KRUSTFLAGS+=--error-format=json" 2>&1 | grep -v "^make"'';
in ''
  ["rust-analyzer"] = {
    cargo = {
      buildScripts = {
        overrideCommand = {"${cmd}"},
      },
    },
    checkOnSave = {
      overrideCommand = {"${cmd}"},
    },
  },
'';
```


### Direnv

If you have nix-direnv enabled a shell with everything you need should open when you `cd` into the directory after calling `direnv allow`
