# -*- coding: utf-8 -*-
"""
Created on Thu Dec  3 08:49:47 2020

@copyright: grammm GmbH, 2020
"""

from cryptography import x509
from cryptography.hazmat.backends import default_backend

from datetime import datetime, MINYEAR, MAXYEAR

import logging


from .config import Config
from .misc import GenericObject, createMapping


class CertificateError(BaseException):
    pass


class GrammmLicense(GenericObject):
    @staticmethod
    def validate(cert):
        if cert is not None and not cert.not_valid_before <= datetime.now() <= cert.not_valid_after:
            raise CertificateError("Certificate expired")

    @property
    def error(self):
        try:
            self.validate(self.cert)
        except CertificateError as err:
            return err.args[0]


def _defaultLicense():
    return GrammmLicense(cert=None,
                         file=None,
                         users=5,
                         product="Community",
                         notBefore=datetime(1000, 1, 1),
                         notAfter=datetime(MAXYEAR, 12, 31, 23, 59, 59))


def _processCertificate(data):
    try:
        cert = x509.load_pem_x509_certificate(data, default_backend())
        GrammmLicense.validate(cert)
        exts = createMapping(cert.extensions, lambda x: x.oid.dotted_string, lambda x: x.value.value)
        lic = GrammmLicense(cert=cert, file=data)
        lic.users = int(exts.get("1.3.6.1.4.1.56504.1.1"))
        lic.product = exts.get("1.3.6.1.4.1.56504.1.2").decode("utf-8") if "1.3.6.1.4.1.56504.1.2" in exts else None
        lic.notBefore = cert.not_valid_before
        lic.notAfter = cert.not_valid_after
        return True, lic
    except ValueError:
        return False, "Bad certificate"
    except CertificateError as err:
        return False, err.args[0]
    except:
        return False, "Unknown error"


def loadCertificate():
    try:
        logging.info("load")
        with open(Config["options"]["licenseFile"], "rb") as file:
            data = file.read()
        success, val = _processCertificate(data)
        if not success:
            logging.error("Failed to load license: "+val)
        else:
            return val
    except KeyError:
        logging.warn("Could not load license: location not configured")
    except FileNotFoundError as err:
        logging.warn("Could not load license: "+err.args[1])


_license = loadCertificate() or _defaultLicense()


def updateCertificate(data):
    success, val = _processCertificate(data)
    if not success:
        return val
    try:
        with open(Config["options"]["licenseFile"], "wb") as file:
            file.write(data)
        global _license
        _license = val
    except KeyError:
        return "Could not load license: location not configured"
    except FileNotFoundError as err:
        return "Could not load license: "
    except PermissionError as err:
        return err.args[1]


def getLicense():
    global _license
    if _license.error:
        _license = _defaultLicense()
    return _license
