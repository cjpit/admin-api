# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2021 grammm GmbH


def domainFilter(domainSpec, *filters):
    from orm.domains import Domains
    from sqlalchemy import and_
    return and_(True if domainSpec is None else
                Domains.ID == domainSpec if domainSpec.isdigit() else
                Domains.domainname.ilike(domainSpec+"%"), *filters)


def domainCandidates(domainSpec, *filters):
    from orm.domains import Domains
    return Domains.query.filter(domainFilter(domainSpec, *filters))


def userFilter(userSpec, *filters):
    from orm.users import Users
    from sqlalachemy import and_
    return and_(True if userSpec is None else
                Users.ID == userSpec if userSpec.isdigit() else
                Users.username.ilike(userSpec+"%"), *filters)


def userCandidates(userSpec, *filters):
    from orm.users import Users
    return Users.query.filter(userFilter(userSpec, *filters))
