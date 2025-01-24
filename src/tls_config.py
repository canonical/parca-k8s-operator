# Copyright 2024 Canonical
# See LICENSE file for licensing details.

"""TLS Config class."""
import dataclasses

from charms.tls_certificates_interface.v4.tls_certificates import CertificateRequestAttributes, ProviderCertificate, \
    PrivateKey


@dataclasses.dataclass
class TLSConfig:
    """TLSConfig."""

    cr: "CertificateRequestAttributes"
    certificate: "ProviderCertificate"
    key: "PrivateKey"
