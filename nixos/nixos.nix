{ src, ... }@args:
import "${src}/lib/mksystem.nix" (
  args
  // {
    systemModules = [
      (
        {
          homePath,
          modulesPath,
          pkgs,
          ...
        }:
        {
          imports = [ "${modulesPath}/virtualisation/qemu-vm.nix" ];

          services = {
            spice-vdagentd.enable = true;
            spice-webdavd.enable = true;
            qemuGuest.enable = true;
          };

          virtualisation = {
            libvirtd.qemu.vhostUserPackages = [ pkgs.virtiofsd ];

            qemu.options = [
              "-device virtio-vga"
              "-m 2048"
            ];
          };

          users.users.vmtest = {
            isNormalUser = true;
            home = "${homePath}/vmtest";
            extraGroups = [ "wheel" ];
            initialPassword = "vmtest";
          };
        }
      )
    ];
    users = { };
  }
)
