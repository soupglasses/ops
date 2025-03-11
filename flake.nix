{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs = {nixpkgs, ...}: let
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
    # -- Development Shells --
    # Scoped environments including packages and shell-hooks to aid project development.

    devShells = eachSystem ({pkgs, ...}: {
      default = pkgs.mkShellNoCC {
        shellHook = ''
          ${pkgs.pre-commit}/bin/pre-commit install --install-hooks --overwrite
        '';
        nativeBuildInputs = with pkgs; [
          pre-commit
          alejandra
          editorconfig-checker
          kubectl
          kubeseal
          fluxcd
        ];
      };
    });
  };
}
