{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs = {
    self,
    nixpkgs,
  }: let
    supportedSystems = [
      "aarch64-linux"
      "x86_64-linux"
    ];
    eachSystem = f:
      nixpkgs.lib.genAttrs supportedSystems (system:
        f {
          inherit system;
          pkgs = nixpkgs.legacyPackages.${system};
        });
  in {
    packages = eachSystem ({pkgs, ...}: {
      flux-local = pkgs.python3Packages.buildPythonPackage rec {
        pname = "flux-local";
        version = "7.3.0";
        pyproject = true;

        src = pkgs.fetchFromGitHub {
          owner = "allenporter";
          repo = pname;
          rev = version;
          hash = "sha256-YzxFw49yMum0F6Z57utUSzxiBK7ME1KxLVqvbdZBHBY=";
        };

        build-system = with pkgs.python3Packages; [
          setuptools
          setuptools-scm
        ];

        dependencies = with pkgs.python3Packages; [
          aiofiles
          nest-asyncio
          gitpython
          pyyaml
          mashumaro
          pytest
          pytest-asyncio
        ];

        buildInputs = with pkgs; [
          kustomize
          fluxcd
          helm
        ];

        postPatch = ''
          substituteInPlace flux_local/kustomize.py \
            --replace-fail 'KUSTOMIZE_BIN = "kustomize"' 'KUSTOMIZE_BIN = "${pkgs.kustomize}/bin/kustomize"' \
            --replace-fail 'FLUX_BIN = "flux"' 'FLUX_BIN = "${pkgs.fluxcd}/bin/flux"'
          substituteInPlace flux_local/helm.py \
            --replace-fail 'HELM_BIN = "helm"' 'HELM_BIN = "${pkgs.helm}/bin/helm"'
          substituteInPlace tests/tool/__init__.py \
            --replace-fail 'FLUX_LOCAL_BIN = "flux-local"' "FLUX_LOCAL_BIN = \"$out/bin/flux-local\""
        '';

        # Majority of tests require git repo.
        doCheck = false;
      };
    });

    # -- Development Shells --
    # Scoped environments including packages and shell-hooks to aid project development.

    devShells = eachSystem ({
      pkgs,
      system,
    }: {
      default = pkgs.mkShellNoCC {
        shellHook = ''
          ${pkgs.pre-commit}/bin/pre-commit install --install-hooks --overwrite
          export KUBECONFIG=~/.kube/home
        '';
        nativeBuildInputs = with pkgs; [
          # Formatting
          pre-commit
          alejandra
          editorconfig-checker
          kubeconform
          yamlfmt
          # Kubernetes
          talosctl
          kubectl
          kubecolor
          kubeseal
          fluxcd
          self.packages.${system}.flux-local
          kustomize
          # Secrets
          age
          sops
        ];
      };
    });
  };
}
