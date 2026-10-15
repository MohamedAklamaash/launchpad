# The mirrored Kaniko (Chainguard fork) image, never pulled from upstream at build time —
# see README "Mirror the Kaniko image" for why and how. Immutable tags mean a tag can never
# be repointed at a different image after the fact; the task definition pins by digest
# anyway (var.kaniko_image_digest), so this is a second, independent guarantee against the
# same class of mistake.
resource "aws_ecr_repository" "kaniko_mirror" {
  name                 = "launchpad-platform-builder-kaniko-mirror"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Name = "launchpad-platform-builder-kaniko-mirror"
  }
}
