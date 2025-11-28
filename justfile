# Setup development environment (install git hooks)
setup:
    #!/usr/bin/env bash
    echo '#!/bin/sh' > .git/hooks/pre-commit
    echo 'just check' >> .git/hooks/pre-commit
    chmod +x .git/hooks/pre-commit
    echo "Git pre-commit hook installed successfully"

# Run all tests
test: test-rust

# Check all code quality tools
[parallel]
check: check-rust check-python check-nix

[parallel]
fix: fix-rust fix-rust fix-nix

fix-nix: fix-fmt-nix

[parallel]
fix-rust: fix-fmt-rust fix-lint-rust

[parallel]
fix-python: fix-fmt-python fix-lint-python

# Fix formatting issues
[parallel]
fix-fmt: fix-fmt-rust fix-fmt-python fix-fmt-nix

# Fix linting issues
[parallel]
fix-lint: fix-lint-rust fix-lint-python

# Check Rust code in kdf-init
[parallel]
check-rust: check-fmt-rust check-lint-rust

# Check Rust formatting
check-fmt-rust:
    cd kdf-init && cargo fmt --check

# Format Rust code
fix-fmt-rust:
    cd kdf-init && cargo fmt

# Run cargo check and clippy
check-lint-rust:
    cd kdf-init && cargo check --all-targets --all-features
    cd kdf-init && cargo clippy --all-targets --all-features -- -D warnings

# Fix clippy warnings
fix-lint-rust:
    cd kdf-init && cargo clippy --all-targets --all-features --fix --allow-dirty --allow-staged

# Run Rust tests
test-rust:
    cd kdf-init && cargo test --all-features

# Check Python code in kdf-cli
[parallel]
check-python: check-fmt-python check-lint-python

# Run ruff linter and ty typechecker
[parallel]
check-lint-python: check-lint-python-ruff check-lint-python-ty

# Run ruff linter
check-lint-python-ruff:
    cd kdf-cli && ruff check .

# Run ty typechecker
check-lint-python-ty:
    cd kdf-cli && ty check .

# Fix ruff issues
fix-lint-python:
    cd kdf-cli && ruff check --fix .

# Check ruff formatting
check-fmt-python:
    cd kdf-cli && ruff format --check .

# Format Python code
fix-fmt-python:
    cd kdf-cli && ruff format .

# Check Nix files
[parallel]
check-nix: check-fmt-nix

# Check Nix formatting
check-fmt-nix:
    nix fmt -- --fail-on-change

# Format Nix files
fix-fmt-nix:
    nix fmt

# Build kdf-init statically with musl
build-init:
    cd kdf-init && cargo build --release --target x86_64-unknown-linux-musl

# Build kdf-init and run with flake kernel
run: build-init
    #!/usr/bin/env bash
    set -e
    echo "Running kdf with flake kernel..."
    mkdir -p .kdf-resources
    cp kdf-init/target/x86_64-unknown-linux-musl/release/init .kdf-resources/init
    echo "Building initramfs..."
    kdf build initramfs .kdf-resources/init --output .kdf-resources/initramfs.cpio
    export KDF_RESOURCE_DIR="$PWD/.kdf-resources"
    kdf run --kernel "$KERNEL_IMG_DIR/bzImage" --nix busybox
