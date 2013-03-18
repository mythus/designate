# Copyright 2012 Managed I.T.
#
# Author: Kiall Mac Innes <kiall@managedit.ie>
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
import re
import jsonschema
import ipaddr
import iso8601
import urllib
import urlparse
from datetime import datetime
from moniker.openstack.common import log as logging
from moniker import exceptions
from moniker import utils

LOG = logging.getLogger(__name__)
_RE_DOMAINNAME = '^(?!.{255,})((?!\-)[A-Za-z0-9_\-]{1,63}(?<!\-)\.)+$'
_RE_HOSTNAME = '^(?!.{255,})((^\*|(?!\-)[A-Za-z0-9_\-]{1,63})(?<!\-)\.)+$'


class StaticResolver(object):
    def __init__(self, store={}):
        self.store = store

    def resolve(self, schema, ref):
        # NOTE(kiall): We're ignoring the `schema` argument here, as we
        #              expect to recieve a non-relative reference.
        uri, fragment = urlparse.urldefrag(ref)

        if uri in self.store:
            document = self.store[uri]
        else:
            raise jsonschema.RefResolutionError(
                "Unresolvable JSON reference: %r" % uri
            )

        return self.resolve_fragment(document, fragment.lstrip("/"))

    def resolve_fragment(self, document, fragment):
        parts = urllib.unquote(fragment).split("/") if fragment else []

        for part in parts:
            part = part.replace("~1", "/").replace("~0", "~")

            if part not in document:
                raise jsonschema.RefResolutionError(
                    "Unresolvable JSON pointer: %r" % fragment
                )

            document = document[part]

        return document


# TODO: We shouldn't hard code this list.. Or define it half way down the page
resolver = StaticResolver(store={
    '/schemas/domain': utils.load_schema('v1', 'domain'),
    '/schemas/domains': utils.load_schema('v1', 'domains'),
    '/schemas/record': utils.load_schema('v1', 'record'),
    '/schemas/records': utils.load_schema('v1', 'records'),
    '/schemas/server': utils.load_schema('v1', 'server'),
    '/schemas/servers': utils.load_schema('v1', 'servers'),
    '/schemas/tsigkey': utils.load_schema('v1', 'tsigkey'),
    '/schemas/tsigkeys': utils.load_schema('v1', 'tsigkeys'),
})


class SchemaValidator(jsonschema.Draft3Validator):
    def validate_type(self, types, instance, schema):
        # NOTE(kiall): A datetime object is not a string, but is still valid.
        if ('format' in schema and schema['format'] == 'date-time'
                and isinstance(instance, datetime)):
            return

        errors = super(SchemaValidator, self).validate_type(types, instance,
                                                            schema)

        for error in errors:
            yield error

    def validate_format(self, format, instance, schema):
        if format == "date-time":
            # ISO 8601 format
            if self.is_type(instance, "string"):
                try:
                    iso8601.parse_date(instance)
                except:
                    msg = "%s is not an ISO 8601 date" % (instance)
                    yield jsonschema.ValidationError(msg)
        elif format == "date":
            # YYYY-MM-DD
            if self.is_type(instance, "string"):
                # TODO: I'm sure there is a more accurate regex than this..
                pattern = ('^[0-9]{4}-(((0[13578]|(10|12))-'
                           '(0[1-9]|[1-2][0-9]|3[0-1]))|'
                           '(02-(0[1-9]|[1-2][0-9]))|((0[469]|11)-'
                           '(0[1-9]|[1-2][0-9]|30)))$')

                if not re.match(pattern, instance):
                    msg = "%s is not a date" % (instance)
                    yield jsonschema.ValidationError(msg)
        elif format == "time":
            # hh:mm:ss
            if self.is_type(instance, "string"):
                # TODO: I'm sure there is a more accurate regex than this..
                pattern = "^(?:(?:([01]?\d|2[0-3]):)?([0-5]?\d):)?([0-5]?\d)$"
                if not re.match(pattern, instance):
                    msg = "%s is not a time" % (instance)
                    yield jsonschema.ValidationError(msg)
            pass
        elif format == "email":
            # A valid email address
            pass
        elif format == "ip-address":
            # IPv4 Address
            if self.is_type(instance, "string"):
                try:
                    ipaddr.IPv4Address(instance)
                except ipaddr.AddressValueError:
                    msg = "%s is not an IPv4 address" % (instance)
                    yield jsonschema.ValidationError(msg)
        elif format == "ipv6":
            # IPv6 Address
            if self.is_type(instance, "string"):
                try:
                    ipaddr.IPv6Address(instance)
                except ipaddr.AddressValueError:
                    msg = "%s is not an IPv6 address" % (instance)
                    yield jsonschema.ValidationError(msg)
        elif format == "host-name":
            # A valid hostname
            if self.is_type(instance, "string"):
                if not re.match(_RE_HOSTNAME, instance):
                    msg = "%s is not a host name" % (instance)
                    yield jsonschema.ValidationError(msg)
        elif format == "domain-name":
            # A valid domainname
            if self.is_type(instance, "string"):
                if not re.match(_RE_DOMAINNAME, instance):
                    msg = "%s is not a domain name" % (instance)
                    yield jsonschema.ValidationError(msg)

    def validate_anyOf(self, schemas, instance, schema):
        for s in schemas:
            if self.is_valid(instance, s):
                return
        else:
            yield jsonschema.ValidationError(
                "%r is not valid for any of listed schemas %r" %
                (instance, schemas)
            )

    def validate_allOf(self, schemas, instance, schema):
        for s in schemas:
            if not self.is_valid(instance, s):
                yield jsonschema.ValidationError(
                    "%r is not valid against %r" % (instance, s)
                )

    def validate_oneOf(self, schemas, instance, schema):
        match = False
        for s in schemas:
            if self.is_valid(instance, s):
                if match:
                    yield jsonschema.ValidationError(
                        "%r matches more than one schema in %r" %
                        (instance, schemas)
                    )
                match = True
        if not match:
            yield jsonschema.ValidationError(
                "%r is not valid for any of listed schemas %r" %
                (instance, schemas)
            )


class Schema(object):
    def __init__(self, version, name):
        self.raw_schema = utils.load_schema(version, name)
        self.validator = SchemaValidator(self.raw_schema, resolver=resolver)

    @property
    def schema(self):
        return self.validator.schema

    @property
    def properties(self):
        return self.schema['properties']

    @property
    def resolver(self):
        return self.validator.resolver

    @property
    def links(self):
        return self.schema['links']

    @property
    def raw(self):
        return self.raw_schema

    def validate(self, obj):
        errors = []

        for error in self.validator.iter_errors(obj):
            errors.append({
                'path': ".".join(reversed(error.path)),
                'message': error.message,
                'validator': error.validator
            })

        if len(errors) > 0:
            raise exceptions.InvalidObject("Provided object does not match "
                                           "schema", errors=errors)

    def filter(self, instance, properties=None):
        if not properties:
            properties = self.properties

        filtered = {}

        for name, subschema in properties.items():
            if 'type' in subschema and subschema['type'] == 'array':
                subinstance = instance.get(name, None)
                filtered[name] = self._filter_array(subinstance, subschema)
            elif 'type' in subschema and subschema['type'] == 'object':
                subinstance = instance.get(name, None)
                properties = subschema['properties']
                filtered[name] = self.filter(subinstance, properties)
            else:
                filtered[name] = instance.get(name, None)

        return filtered

    def _filter_array(self, instance, schema):
        if 'items' in schema and isinstance(schema['items'], list):
            # TODO: We currently don't make use of this..
            raise NotImplementedError()

        elif 'items' in schema:
            schema = schema['items']

            if '$ref' in schema:
                schema = self.resolver.resolve(self.schema, schema['$ref'])

            properties = schema['properties']

            return [self.filter(i, properties) for i in instance]

        elif 'properties' in schema:
            schema = schema['properties']

            if '$ref' in schema:
                schema = self.resolver.resolve(self.schema, schema['$ref'])

            return [self.filter(i, schema) for i in instance]

        else:
            raise NotImplementedError('Can\'t filter unknown array type')
