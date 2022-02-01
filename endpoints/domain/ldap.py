# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2020 grommunio GmbH

import shutil

from flask import jsonify, request

import api
from api.core import API, secure
from api.security import checkPermissions

from services import Service
from tools.DataModel import InvalidAttributeError, MismatchROError
from tools.permissions import SystemAdminPermission, SystemAdminROPermission, DomainAdminPermission, DomainAdminROPermission
from tools.tasq import TasQServer

from orm import DB


@API.route(api.BaseRoute+"/domains/ldap/search", methods=["GET"])
@secure(requireDB=True, authLevel="user", service="ldap")
def searchLdap(ldap):
    checkPermissions(DomainAdminROPermission("*"))
    from orm.domains import Domains
    if "query" not in request.args or len(request.args["query"]) < 3:
        return jsonify(message="Missing or too short query"), 400
    permissions = request.auth["user"].permissions()
    if "domain" in request.args:
        domainIDs = [int(ID) for ID in request.args["domain"].split(",") if DomainAdminROPermission(int(ID)) in permissions]
        if len(domainIDs) == 0:
            return jsonify(data=[])
    if SystemAdminPermission() in permissions:
        domainFilters = ()
    else:
        domainIDs = {permission.domainID for permission in permissions if isinstance(permission, DomainAdminROPermission)}
        if len(domainIDs) == 0:
            return jsonify(data=[])
        domainFilters = () if "*" in domainIDs else (Domains.ID.in_(domainIDs),)
    domainNames = [d[0] for d in Domains.query.filter(*domainFilters).with_entities(Domains.domainname).all()]\
        if len(domainFilters) else None
    ldapusers = ldap.searchUsers(request.args["query"], domainNames)
    return jsonify(data=[{"ID": ldap.escape_filter_chars(u.ID), "name": u.name, "email": u.email} for u in ldapusers])


def ldapDownsyncDomains(ldap, domain, lang=None):
    """Synchronize ldap domains.

    Parameters
    ----------
    domain : orm.domains.Domains
        Domain object providing ID and domainname. If None, synchronize all domains.
    """
    params = {} if domain is None else {"domains": ({"ID": domain.ID, "domainname": domain.domainname},)}
    params["lang"] = lang
    params["import"] = request.args.get("import") == "true"
    task = TasQServer.create("ldapSync", params,
                             permission=DomainAdminROPermission(domain.ID) if domain else SystemAdminROPermission())
    timeout = float(request.args.get("timeout", 1))
    if timeout > 0:
        TasQServer.wait(task.ID, timeout)
    if not task.done:
        return jsonify(message="Created background task #"+str(task.ID), taskID=task.ID), 202
    if task.state == task.COMPLETED:
        return jsonify(message=task.message,
                       data=task.params.get("result", []))
    return jsonify(message="Synchronization failed: "+task.message), 500


@API.route(api.BaseRoute+"/domains/ldap/downsync", methods=["POST"])
@secure(requireDB=True, authLevel="user", service="ldap")
def ldapDownsyncAll(ldap):
    checkPermissions(SystemAdminPermission())
    return ldapDownsyncDomains(ldap, None, request.args.get("lang", ""))


@API.route(api.BaseRoute+"/domains/<int:domainID>/ldap/downsync", methods=["POST"])
@secure(requireDB=True, authLevel="user", service="ldap")
def ldapDownsyncDomain(ldap, domainID):
    checkPermissions(DomainAdminPermission(domainID))
    from orm.domains import Domains
    domain = Domains.query.with_entities(Domains.ID, Domains.domainname).filter(Domains.ID == domainID).first()
    if domain is None:
        return jsonify(message="Domain not found"), 404
    return ldapDownsyncDomains(ldap, domain, request.args.get("lang", ""))


@API.route(api.BaseRoute+"/domains/ldap/importUser", methods=["POST"])
@secure(requireDB=True, authLevel="user", service="ldap")
def downloadLdapUser(ldap):
    checkPermissions(DomainAdminPermission("*"))
    from orm.domains import Domains
    from orm.users import Users
    if "ID" not in request.args:
        return jsonify(message="Missing ID"), 400
    try:
        ID = ldap.unescapeFilterChars(request.args["ID"])
    except Exception:
        return jsonify(message="Invalid ID"), 400
    force = request.args.get("force")
    lang = request.args.get("lang", "")
    userinfo = ldap.getUserInfo(ID)
    if userinfo is None:
        return jsonify(message="User not found"), 404
    domain = Domains.query.filter(Domains.domainname == userinfo.email.split("@")[1]).with_entities(Domains.ID).first()
    if domain is None:
        return jsonify(message="Cannot import user: Domain not found"), 400
    if not DomainAdminPermission(domain.ID) in request.auth["user"].permissions():
        return jsonify(message="User not found"), 404
    user = Users.query.filter(Users.externID == ID).first() or\
           Users.query.filter(Users.username == userinfo.email).first()
    if user is not None:
        if user.externID != ID and not force == "true":
            return jsonify(message="Cannot import user: User exists " +
                           ("locally" if user.externID is None else "and is associated with another LDAP object")), 409
        checkPermissions(DomainAdminPermission(user.domainID))
        userdata = ldap.downsyncUser(ID, user.properties)
        try:
            user.fromdict(userdata)
            user.externID = ID
            user.lang = user.lang or lang
            DB.session.commit()
            return jsonify(user.fulldesc()), 200
        except (InvalidAttributeError, MismatchROError, ValueError) as err:
            DB.session.rollback()
            return jsonify(message=err.args[0]), 500
    userdata = ldap.downsyncUser(ID)
    if userdata is None:
        return jsonify(message="Error retrieving user"), 404
    userdata["lang"] = lang
    result, code = Users.create(userdata, externID=ID)
    if code != 201:
        return jsonify(message="Failed to create user: "+result), code
    checkPermissions(DomainAdminPermission(result.domainID))
    DB.session.add(result)
    DB.session.commit()
    return jsonify(result.fulldesc()), 201


@API.route(api.BaseRoute+"/domains/<int:domainID>/users/<int:userID>/downsync", methods=["PUT"])
@secure(requireDB=True, authLevel="user", service="ldap")
def updateLdapUser(ldap, domainID, userID):
    checkPermissions(DomainAdminPermission(domainID))
    from orm.users import Users
    user = Users.query.filter(Users.ID == userID, Users.domainID == domainID).first()
    if user is None:
        return jsonify(message="User not found"), 404
    ldapID = ldap.unescapeFilterChars(request.args["ID"]) if "ID" in request.args else user.externID
    if ldapID is None:
        return jsonify(message="Cannot synchronize user: Could not determine LDAP object"), 400
    userdata = ldap.downsyncUser(ldapID, user.properties)
    if userdata is None:
        return jsonify(message="Cannot synchronize user: LDAP object not found"), 404
    user.fromdict(userdata)
    user.externID = ldapID
    user.lang = user.lang or request.args.get("lang", "")
    DB.session.commit()
    return jsonify(user.fulldesc())


@API.route(api.BaseRoute+"/domains/ldap/check", methods=["GET", "DELETE"])
@secure(requireDB=True, authLevel="user", service="ldap")
def checkLdapUsers(ldap):
    checkPermissions(DomainAdminROPermission("*") if request.method == "GET" else DomainAdminPermission("*"))
    from orm.users import Users
    permissions = request.auth["user"].permissions()
    if SystemAdminPermission in permissions:
        domainFilter = ()
    else:
        domainIDs = {permission.domainID for permission in permissions if isinstance(permission, DomainAdminROPermission)}
        domainFilter = (Users.domainID == domainID for domainID in domainIDs)
    users = Users.query.filter(Users.externID != None, *domainFilter)\
                       .with_entities(Users.ID, Users.username, Users.externID, Users.maildir)\
                       .all()
    if len(users) == 0:
        return jsonify(message="No LDAP users found", **{"orphaned" if request.method == "GET" else "deleted": []})
    orphaned = [user for user in users if ldap.getUserInfo(user.externID) is None]
    if len(orphaned) == 0:
        return jsonify(message="All LDAP users are valid", **{"orphaned" if request.method == "GET" else "deleted": []})
    orphanedData = [{"ID": user.ID, "username": user.username} for user in orphaned]
    if request.method == "GET":
        return jsonify(orphaned=orphanedData)
    deleteMaildirs = request.args.get("deleteFiles") == "true"
    if len(orphaned):
        with Service("exmdb", Service.SUPPRESS_INOP) as exmdb:
            client = exmdb.ExmdbQueries(exmdb.host, exmdb.port, orphaned[0].maildir, True)
            for user in orphaned:
                client.unloadStore(user.maildir)
    if deleteMaildirs:
        for user in orphaned:
            shutil.rmtree(user.maildir, ignore_errors=True)
    Users.query.filter(Users.ID.in_(user.ID for user in orphaned)).delete(synchronize_session=False)
    DB.session.commit()
    return jsonify(deleted=orphanedData)


@API.route(api.BaseRoute+"/domains/ldap/dump", methods=["GET"])
@secure(requireDB=True, authLevel="user", service="ldap")
def dumpLdapUsers(ldap):
    checkPermissions(DomainAdminROPermission("*"))
    try:
        ID = ldap.unescapeFilterChars(request.args["ID"])
    except BaseException:
        return jsonify(message="Invalid ID"), 400
    ldapuser = ldap.dumpUser(ID)
    if ldapuser is None:
        return jsonify(message="User not found"), 404
    return jsonify(data=str(ldapuser))
