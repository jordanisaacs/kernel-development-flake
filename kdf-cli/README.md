# kdf-cli

Command-line tools for the Kernel Development Flake (kdf) project.

## Features

- **Build initramfs**: Create initramfs cpio archives with kdf-init and optional kernel modules
- **Run QEMU**: Launch QEMU VMs with kernel, initramfs, and virtiofs support
- **Module management**: Automatic dependency resolution and load ordering for kernel modules
- **Caching**: Smart caching of initramfs builds based on binary hashing

## Usage

### Build initramfs

```bash
kdf build initramfs /path/to/init --output initramfs.cpio
```

With kernel modules:

```bash
kdf build initramfs /path/to/init --module virtiofs.ko.xz --module fuse.ko.xz
```

### Run QEMU

```bash
kdf run --kernel bzImage --initramfs initramfs.cpio --virtiofs share:/host/path:/guest/path
```

With virtiofs DAX for better performance:

```bash
kdf run --kernel bzImage --initramfs initramfs.cpio --virtiofs share:/host/path:/guest/path --virtiofs-dax
```

## Requirements

Runtime dependencies (provided by Nix):
- QEMU (qemu-system-x86_64)
- virtiofsd
- Standard Unix utilities (cp, chmod, xz, gzip, cpio, find)
- kmod (modinfo)
