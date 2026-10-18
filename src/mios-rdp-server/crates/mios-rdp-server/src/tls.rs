//! TLS acceptor (rustls, AEAD suites only, TLS 1.2/1.3 per [remote.rdp].tls_protocols) for the
//! existing certificate under [remote.rdp].cert_dir: the same one xrdp presents, so the clients'
//! trust decisions carry over. No key log, no client certificates.
#![forbid(unsafe_code)]

use std::sync::Arc;

use anyhow::{Context as _, Result, anyhow, bail};
use tokio_rustls::TlsAcceptor;
use tokio_rustls::rustls;
use tokio_rustls::rustls::pki_types::pem::PemObject as _;
use tokio_rustls::rustls::pki_types::{CertificateDer, PrivateKeyDer};

pub fn versions(spec: &str) -> Result<Vec<&'static rustls::SupportedProtocolVersion>> {
    let mut v = Vec::new();
    for p in spec.split(',').map(str::trim).filter(|p| !p.is_empty()) {
        match p {
            "TLSv1.2" => v.push(&rustls::version::TLS12),
            "TLSv1.3" => v.push(&rustls::version::TLS13),
            other => bail!("unsupported TLS protocol {other:?} (TLSv1.2, TLSv1.3)"),
        }
    }
    if v.is_empty() {
        bail!("no TLS protocol enabled");
    }
    Ok(v)
}

pub fn acceptor(cert_pem: &[u8], key_pem: &[u8], protocols: &str) -> Result<(TlsAcceptor, String)> {
    let certs: Vec<CertificateDer<'static>> = CertificateDer::pem_slice_iter(cert_pem)
        .collect::<Result<_, _>>()
        .map_err(|e| anyhow!("certificate PEM: {e:?}"))?;
    if certs.is_empty() {
        bail!("no certificate in PEM");
    }
    let key = PrivateKeyDer::from_pem_slice(key_pem).map_err(|e| anyhow!("key PEM: {e:?}"))?;
    let fp = sha256_hex(certs[0].as_ref());
    let cfg = rustls::ServerConfig::builder_with_protocol_versions(&versions(protocols)?)
        .with_no_client_auth()
        .with_single_cert(certs, key)
        .context("certificate/key pair")?;
    Ok((TlsAcceptor::from(Arc::new(cfg)), fp))
}

/// SHA-256 of the DER certificate (fingerprint for logs and test A3).
pub fn sha256_hex(data: &[u8]) -> String {
    use sha2::Digest as _;
    sha2::Sha256::digest(data).iter().map(|b| format!("{b:02x}")).collect()
}
