# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2020 grommunio GmbH
"""
Module containing admin Managed CONFigurations
"""

import dbus
import logging

from .config import Config
from .misc import setDirectoryOwner, setDirectoryPermission
from .systemd2 import Systemd

LDAP = {}
AUTHMGR = {}


_ldapDepServices = ("gromox-http.service", "gromox-midb.service", "gromox-zcore.service", "gromox-delivery.service",
                    "gromox-smtp.service", "gromox-imap.service", "gromox-pop3.service")

def _loadConf(path):
    from multidict import MultiDict
    with open(path) as file:
        conf = MultiDict()
        for line in file:
            if line.strip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            conf.add(key.strip(), value.strip())
    return conf


def _fDumpConf(file, conf):
    file.write("# Configuration automatically generated by grommunio-admin.\n")
    for key, value in conf.items():
        if isinstance(value, list):
            file.writelines(("{}={}\n".format(key, entry) for entry in value))
        else:
            file.write("{}={}\n".format(key, value))


def _dumpConf(path, conf):
    with open(path, "w") as file:
        _fDumpConf(file, conf)
    uid = Config["mconf"].get("fileUid", Config["options"].get("fileUid"))
    gid = Config["mconf"].get("fileGid", Config["options"].get("fileGid"))
    prm = Config["mconf"].get("filePermissions", Config["options"].get("filePermissions"))
    setDirectoryOwner(path, uid, gid)
    setDirectoryPermission(path, prm)


def _addIfDef(dc, d, sc, s, all=False, type=None):
    def tf(v):
        return v if type is None else type(v)
    if s in sc:
        dc[d] = tf(sc[s]) if not all else [tf(v) for v in sc.getall(s)]


def _reloadServices(*services):
    for service in services:
        _, msg = Systemd(system=True).tryReloadRestartService(service)
        if msg:
            logging.warn("Failed to reload/restart '{}': {}".format(service, msg))


###############################################################################


def _transformLdap(conf):
    LDAP = {"connection": {}, "users": {}}
    _addIfDef(LDAP, "disabled", conf, "ldap_disabled", type=lambda x: x.lower() in ("true", "yes", "1"))
    _addIfDef(LDAP["connection"], "server", conf, "ldap_host")
    _addIfDef(LDAP["connection"], "bindUser", conf, "ldap_bind_user")
    _addIfDef(LDAP["connection"], "bindPass", conf, "ldap_bind_pass")
    _addIfDef(LDAP["connection"], "starttls", conf, "ldap_start_tls", type=lambda x: x.lower() in ("true", "yes", "1"))
    _addIfDef(LDAP, "baseDn", conf, "ldap_search_base")
    _addIfDef(LDAP, "objectID", conf, "ldap_object_id")
    _addIfDef(LDAP["users"], "username", conf, "ldap_mail_attr")
    _addIfDef(LDAP["users"], "filters", conf, "ldap_user_filters", all=True)
    _addIfDef(LDAP["users"], "filter", conf, "ldap_user_filter")
    _addIfDef(LDAP["users"], "searchAttributes", conf, "ldap_user_search_attrs", all=True)
    _addIfDef(LDAP["users"], "displayName", conf, "ldap_user_displayname")
    _addIfDef(LDAP["users"], "defaultQuota", conf, "ldap_user_default_quota", type=int)
    _addIfDef(LDAP["users"], "templates", conf, "ldap_user_templates", all=True)
    _addIfDef(LDAP["users"], "aliases", conf, "ldap_user_aliases")
    if "ldap_user_attributes" in conf:
        LDAP["users"]["attributes"] = {entry.split(" ", 1)[0]: entry.split(" ", 1)[1]
                                       for entry in conf.getall("ldap_user_attributes") if " " in entry}
    return LDAP


def _flattenLdap(conf):
    LDAP = {}
    _addIfDef(LDAP, "ldap_disabled", conf, "disabled")
    if "connection" in conf:
        _addIfDef(LDAP, "ldap_host", conf["connection"], "server")
        _addIfDef(LDAP, "ldap_bind_user", conf["connection"], "bindUser")
        _addIfDef(LDAP, "ldap_bind_pass", conf["connection"], "bindPass")
        _addIfDef(LDAP, "ldap_start_tls", conf["connection"], "starttls")
    _addIfDef(LDAP, "ldap_search_base", conf, "baseDn")
    _addIfDef(LDAP, "ldap_object_id", conf, "objectID")
    if "users" in conf:
        _addIfDef(LDAP, "ldap_mail_attr", conf["users"], "username")
        _addIfDef(LDAP, "ldap_user_displayname", conf["users"], "displayName")
        _addIfDef(LDAP, "ldap_user_filters", conf["users"], "filters")
        _addIfDef(LDAP, "ldap_user_filter", conf["users"], "filter")
        _addIfDef(LDAP, "ldap_user_search_attrs", conf["users"], "searchAttributes")
        _addIfDef(LDAP, "ldap_user_default_quota", conf["users"], "defaultQuota")
        _addIfDef(LDAP, "ldap_user_templates", conf["users"], "templates")
        _addIfDef(LDAP, "ldap_user_aliases", conf["users"], "aliases")
        if "attributes" in conf["users"]:
            LDAP["ldap_user_attributes"] = ["{} {}".format(key, value) for key, value in conf["users"]["attributes"].items()]
    return LDAP


def loadLdap():
    """(Re)load LDAP configuration from disk.

    Note that this function only populates the configuration but does not validate or deploy it to the ldap module.

    Returns
    -------
    str
        Error message or None if successful
    """
    if "ldapPath" not in Config["mconf"]:
        return "mconf.ldapPath not set"
    try:
        global LDAP
        LDAP = _transformLdap(_loadConf(Config["mconf"]["ldapPath"]))
    except Exception as err:
        return " - ".join((str(arg) for arg in err.args))


def dumpLdap(conf=None, file=None, reloadServices=True):
    """Write LDAP configuration to disk.

    Parameters
    ----------
    conf : dict, optional
        New LDAP configuration or None to use current config.

    Returns
    -------
    str
        Error message or None if successful
    """
    if "ldapPath" not in Config["mconf"]:
        return "mconf.ldapPath not set"
    try:
        global LDAP
        if conf is not None:
            LDAP = conf
        if file is None:
            _dumpConf(Config["mconf"]["ldapPath"], _flattenLdap(LDAP))
            if reloadServices:
                _reloadServices(*_ldapDepServices)
        else:
            _fDumpConf(file, _flattenLdap(LDAP))
    except Exception as err:
        return " - ".join((str(arg) for arg in err.args))


###############################################################################


def loadAuthmgr():
    """(Re)load authmgr configuration from disk.

    Returns
    -------
    str
        Error message or None if successful
    """
    if "authmgrPath" not in Config["mconf"]:
        return "mconf.authmgrPath not set"
    try:
        global AUTHMGR
        rconf = _loadConf(Config["mconf"]["authmgrPath"])
        AUTHMGR = {"authBackendSelection": rconf.get("auth_backend_selection", "always_mysql")}
    except Exception as err:
        return " - ".join((str(arg) for arg in err.args))


def dumpAuthmgr(conf=None, file=None, reloadServices=False):
    """Write authmgr configuration to disk.

    Parameters
    ----------
    conf : dict, optional
        New authmgr configuration or None to use current config.

    Returns
    -------
    str
        Error message or None if successful
    """
    if "authmgrPath" not in Config["mconf"]:
        return "mconf.authmgrPath not set"
    try:
        global AUTHMGR
        if conf is not None:
            AUTHMGR = conf
        wconf = {"auth_backend_selection": AUTHMGR.get("authBackendSelection", "always_mysql")}
        if file is None:
            _dumpConf(Config["mconf"]["authmgrPath"], wconf)
            if reloadServices:
                _reloadServices(*_ldapDepServices)
        else:
            _fDumpConf(file, wconf)
    except Exception as err:
        return " - ".join((str(arg) for arg in err.args))


###############################################################################


def load():
    error = loadLdap()
    if error:
        logging.warn("[MCONF] Could not load ldap config: "+error)
    error = loadAuthmgr()
    if error:
        logging.warn("[MCONF] Could not load authmgr config: "+error)

load()
