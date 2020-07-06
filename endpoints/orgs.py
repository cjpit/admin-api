# -*- coding: utf-8 -*-
"""
Created on Tue Jun 23 17:02:02 2020

@author: Julia Schroeder, julia.schroeder@grammm.com
@copyright: _Placeholder_copyright_
"""


from flask import request, jsonify
from sqlalchemy.exc import IntegrityError

import os
import shutil

from . import defaultListHandler, defaultObjectHandler

import api
from api import API

from tools.misc import AutoClean
from tools.storage import DomainSetup

from orm import DB
if DB is not None:
    from orm.orgs import Orgs, Domains, Aliases
    from orm.ext import AreaList


@API.route(api.BaseRoute+"/orgs", methods=["GET", "POST"])
@api.secure(requireDB=True)
def orgListEndpoint():
    return defaultListHandler(Orgs)


@API.route(api.BaseRoute+"/orgs/<int:ID>", methods=["GET", "PATCH", "DELETE"])
@api.secure(requireDB=True)
def orgObjectEndpoint(ID):
    return defaultObjectHandler(Orgs, ID, "Org")


@API.route(api.BaseRoute+"/domains", methods=["GET"])
@api.secure(requireDB=True)
def domainListEndpoint():
    return defaultListHandler(Domains)


@API.route(api.BaseRoute+"/domains", methods=["POST"])
@api.secure(requireDB=True)
def domainCreate():
    def rollback():
        DB.session.rollback()
    data = request.get_json(silent=True) or {}
    areaID = data.get("areaID")
    domain = defaultListHandler(Domains, result="object")
    if not isinstance(domain, Domains):
        return domain  # If the return value is not a domain, it is an error response
    area = AreaList.query.filter(AreaList.dataType == AreaList.DOMAIN, AreaList.ID == areaID).first()
    try:
        with AutoClean(rollback):
            DB.session.add(domain)
            DB.session.flush()
            with DomainSetup(domain, area) as ds:
                ds.run()
            if not ds.success:
                return jsonify(message="Error during domain setup", error=ds.error),  ds.errorCode
            DB.session.commit()
            return jsonify(domain.fulldesc()), 201
    except IntegrityError as err:
        return jsonify(message="Object violates database constraints", error=err.orig.args[1]), 400


@API.route(api.BaseRoute+"/domains/<int:ID>", methods=["GET", "PATCH", "DELETE"])
@api.secure(requireDB=True)
def domainObjectEndpoint(ID):
    return defaultObjectHandler(Domains, ID, "Domain")


@API.route(api.BaseRoute+"/aliases", methods=["GET", "POST"])
@api.secure(requireDB=True)
def aliasListEndpoint():
    return defaultListHandler(Aliases)


@API.route(api.BaseRoute+"/aliases/<int:ID>", methods=["GET", "PATCH", "DELETE"])
@api.secure(requireDB=True)
def aliasObjectEndpoint(ID):
    return defaultObjectHandler(Aliases, ID, "Alias")
