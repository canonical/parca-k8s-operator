# Copyright 2024 Canonical
# See LICENSE file for licensing details.

"""TLS Config class."""

import dataclasses

from charms.tls_certificates_interface.v4.tls_certificates import (
    CertificateRequestAttributes,
    PrivateKey,
    ProviderCertificate,
)


from typing import List, Optional

import pydantic


# FIXME: https://github.com/canonical/cos-lib/issues/119
#  This is a copy of cosl.coordinated_workers.coordinator.S3ConnectionInfo
#  we do this to avoid bringing in all the charm lib dependencies that go
#  with cosl's coordinated-workers module
class S3Config(pydantic.BaseModel):
    """Model for the s3 relation databag, as returned by the s3 charm lib."""

    # they don't use it, we do

    model_config = {"populate_by_name": True}

    endpoint: str
    bucket: str
    access_key: str = pydantic.Field(alias="access-key")  # type: ignore
    secret_key: str = pydantic.Field(alias="secret-key")  # type: ignore

    region: Optional[str] = pydantic.Field(None)  # type: ignore
    tls_ca_chain: Optional[List[str]] = pydantic.Field(None, alias="tls-ca-chain")  # type: ignore

    @property
    def ca_cert(self) -> Optional[str]:
        """Unify the ca chain provided by the lib into a single cert."""
        return "\n\n".join(self.tls_ca_chain) if self.tls_ca_chain else None


@dataclasses.dataclass
class TLSConfig:
    """Model ."""

    cr: "CertificateRequestAttributes"
    certificate: "ProviderCertificate"
    key: "PrivateKey"
