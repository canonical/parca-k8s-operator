# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

"""The `parca` machine charm has been deprecated.

This library has been transferred to the `parca-k8s` charm.
If you are using this library, please replace it with the new one:

    charmcraft fetch-lib charms.parca-k8s.v0.parca_config
"""

# The unique Charmhub library identifier, never change it
LIBID = "96af36467bb844d7ab8447058ebbc73a"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 6


raise ImportError(
    "This library has been migrated to the `parca-k8s` charm. "
    "Delete it and replace it with the equivalent `charms.parca-k8s.v0.parca_config`."
)