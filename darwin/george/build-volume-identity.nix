# Single authority for the Build volume's mount identity and its projections.
let
  name = "Build";
in
{
  inherit name;
  path = "/Volumes/${name}";
}
