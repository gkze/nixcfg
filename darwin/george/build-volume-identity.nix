# Single authority for the Build volume's mount identity and its projections.
let
  name = "Build";
in
{
  inherit name;
  path = "/Volumes/${name}";
  # Backing store, relative to the user's home directory. A sparse bundle only
  # occupies the bands it has written, so `maxSize` is a ceiling, not an
  # allocation; it matches the 200 GiB bundle argus was created with.
  bundle = "${name}.sparsebundle";
  maxSize = "200g";
}
