# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared fixtures for manager unit tests.

All fixtures produce plain objects or mocks — no Django DB access.
"""

import uuid
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_project_id():
    """Return a deterministic UUID for reproducible path tests."""
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def fake_asset_id():
    return uuid.UUID("aabbccdd-aabb-ccdd-aabb-ccddeeff0011")


@pytest.fixture
def mock_project(fake_project_id):
    """Mock Project with an id attribute."""
    project = MagicMock()
    project.id = fake_project_id
    project.name = "Test Project"
    project.blender_version = MagicMock()
    project.blender_version.series = "4.2"
    project.blender_version.resolved_version = "4.2.19"
    return project


@pytest.fixture
def mock_asset(mock_project, fake_asset_id):
    """Mock Asset linked to mock_project."""
    asset = MagicMock()
    asset.id = fake_asset_id
    asset.name = "Test Asset"
    asset.project = mock_project
    asset.project_id = mock_project.id
    asset.blend_file = MagicMock()
    asset.blend_file.name = "assets/12345678/abcd1234.blend"
    return asset


@pytest.fixture
def mock_job(mock_asset):
    """Mock Job linked to mock_asset, no animation or tiled_job."""
    job = MagicMock()
    job.id = 42
    job.pk = 42
    job.name = "Test Job 001"
    job.asset = mock_asset
    job.animation = None
    job.tiled_job = None
    job.output_file = MagicMock()
    job.output_file.name = "outputs/test.png"
    return job


@pytest.fixture
def mock_animation(mock_project, mock_asset):
    """Mock Animation linked to mock_project/asset."""
    anim = MagicMock()
    anim.id = 99
    anim.name = "Walk Cycle"
    anim.project = mock_project
    anim.project_id = mock_project.id
    anim.asset = mock_asset
    return anim


@pytest.fixture
def mock_animation_frame(mock_animation):
    """Mock AnimationFrame linked to mock_animation."""
    frame = MagicMock()
    frame.animation = mock_animation
    frame.frame_number = 5
    return frame


@pytest.fixture
def mock_tiled_job(mock_project, mock_asset):
    """Mock TiledJob linked to mock_project/asset."""
    tj = MagicMock()
    tj.id = uuid.UUID("99999999-0000-0000-0000-000000000001")
    tj.pk = tj.id
    tj.name = "Tiled Render"
    tj.project = mock_project
    tj.project_id = mock_project.id
    tj.asset = mock_asset
    return tj


@pytest.fixture
def mock_request():
    """Create a mock DRF request with configurable auth."""
    request = MagicMock()
    request.user = MagicMock()
    request.auth = None
    request.method = "GET"
    return request


@pytest.fixture
def mock_view():
    """Minimal mock DRF view."""
    return MagicMock()


@pytest.fixture
def mock_file_field():
    """A mock Django FieldFile with an open() context manager."""
    field = MagicMock()
    field.name = "outputs/render_001.png"
    return field


# --- Certificate test helpers ---

def generate_test_keypair():
    """Generate a small RSA keypair for fast tests."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )


def build_test_cert(key, days_valid=3650, backdate_hours=1):
    """Build a self-signed cert from a key with configurable validity."""
    from datetime import datetime, timedelta, timezone
    from ipaddress import IPv4Address
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID

    now = datetime.now(timezone.utc)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "test-host"),
    ])
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=backdate_hours))
        .not_valid_after(now + timedelta(days=days_valid))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.IPAddress(IPv4Address("127.0.0.1")),
                x509.DNSName("localhost"),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )


def write_cert_and_key(cert, key, cert_path, key_path):
    """Write PEM-encoded cert and key to disk."""
    from cryptography.hazmat.primitives import serialization
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
