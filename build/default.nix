{
  pkgs,
  lib ? pkgs.lib,
}:
{
  buildCModule = pkgs.callPackage ./c-module.nix { };
  buildRustModule = pkgs.callPackage ./rust-module.nix { };

  buildInitramfs = pkgs.callPackage ./initramfs.nix { };

  buildKernelConfigModule = pkgs.callPackage ./kernel-config-module.nix { };
  buildKernelConfig = pkgs.callPackage ./kernel-config.nix { };
  buildKernel = pkgs.callPackage ./kernel.nix { };

  buildQemuCmd = pkgs.callPackage ./run-qemu.nix { };
  buildGdbCmd = pkgs.callPackage ./run-gdb.nix { };
}
