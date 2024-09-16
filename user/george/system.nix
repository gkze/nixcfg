{
  services.coredns = {
    enable = true;
    config = ''
      . {
        forward . 1.1.1.1 1.0.0.1 8.8.8.8 8.8.4.4
        cache
      }

      local {
        template IN A {
          answer "{{ .Name }} 0 IN A 127.0.0.1"
        }
      }
    '';
  };
  networking.networkmanager.insertNameservers = [ "127.0.0.1" ];
}
