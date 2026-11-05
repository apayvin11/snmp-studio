# SPDX-License-Identifier: Apache-2.0
"""Parse SMI source as data; never execute generated Python from imported MIBs."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import re
from pysmi.parser import SmiStarParser

BASE = {'iso': (1,), 'org': (1,3), 'dod': (1,3,6), 'internet': (1,3,6,1),
        'directory': (1,3,6,1,1), 'mgmt': (1,3,6,1,2), 'mib-2': (1,3,6,1,2,1),
        'transmission': (1,3,6,1,2,1,10), 'experimental': (1,3,6,1,3),
        'private': (1,3,6,1,4), 'enterprises': (1,3,6,1,4,1), 'security': (1,3,6,1,5),
        'snmpV2': (1,3,6,1,6), 'snmpDomains': (1,3,6,1,6,1),
        'snmpProxys': (1,3,6,1,6,2), 'snmpModules': (1,3,6,1,6,3), 'zeroDotZero': (0,0)}
BUILTIN_MODULES = {'SNMPv2-SMI', 'SNMPv2-TC', 'SNMPv2-CONF', 'RFC1155-SMI', 'RFC-1212', 'RFC-1215', 'SNMPv2-TM'}
MAX_MIB = 4 * 1024 * 1024


@dataclass
class Diagnostic:
    severity: str
    message: str
    line: int = 1


@dataclass
class MibObject:
    module: str
    name: str
    kind: str
    oid: str = ''
    syntax: str = ''
    access: str = ''
    description: str = ''
    units: str = ''
    line: int = 1
    enums: dict = field(default_factory=dict)
    declaration: tuple = ()


def parts(node, tag):
    if isinstance(node, (tuple, list)):
        if node and node[0] == tag:
            yield node
        for child in node:
            if isinstance(child, (tuple, list)):
                yield from parts(child, tag)


def parse_source(text):
    if len(text.encode('utf-8')) > MAX_MIB:
        return [], {}, [Diagnostic('error', 'MIB exceeds 4 MiB')]
    try:
        ast = SmiStarParser().parse(text)
    except Exception as exc:
        line = getattr(exc, 'lineno', 0) or getattr(exc, 'line', 0)
        if not line:
            match = re.search(r'(?:line|lineno)[ :=]+(\d+)', str(exc), re.I)
            line = int(match[1]) if match else 1
        return [], {}, [Diagnostic('error', str(exc), line)]
    objects, imports = [], {}
    for module, module_oid, imported, declarations in ast:
        imports[module] = imported
        for decl in declarations or []:
            if not isinstance(decl,(tuple,list)) or len(decl) < 2:
                continue
            name = decl[1]
            found = re.search(r'(?m)^\s*'+re.escape(name)+r'\b', text)
            line = text[:found.start()].count('\n')+1 if found else 1
            def value(tag):
                values = list(parts(decl, tag))
                return str(values[0][1]) if values and len(values[0]) > 1 else ''
            syntax = value('SimpleSyntax') or value('ApplicationSyntax')
            enums = {}
            for enum in parts(decl, 'enumSpec'):
                for pair in enum[1]:
                    if isinstance(pair, tuple) and len(pair) == 2:
                        enums[int(pair[1])] = pair[0]
            kind={'moduleIdentityClause':'MODULE-IDENTITY','objectTypeClause':'OBJECT-TYPE','valueDeclaration':'OBJECT IDENTIFIER','typeDeclaration':'TYPE','notificationTypeClause':'NOTIFICATION-TYPE','objectIdentityClause':'OBJECT-IDENTITY','trapTypeClause':'TRAP-TYPE','objectGroupClause':'OBJECT-GROUP','notificationGroupClause':'NOTIFICATION-GROUP','moduleComplianceClause':'MODULE-COMPLIANCE','agentCapabilitiesClause':'AGENT-CAPABILITIES'}.get(decl[0],str(decl[0]))
            objects.append(MibObject(module, name, kind, syntax=syntax,
                access=value('MaxAccessPart') or value('AccessPart'), description=value('DESCRIPTION'),
                units=value('UNITS'), line=line, enums=enums, declaration=decl))
    return objects, imports, []


class MibIndex:
    def __init__(self):
        self.objects = []
        self.diagnostics = {}
        self.imports = {}
        self._notification_cache = {}

    def rebuild(self, sources):
        self.objects, self.diagnostics = [], {}
        self._notification_cache = {}
        imports, owners = {}, {}
        for path, text in sources.items():
            objects, imported, errors = parse_source(text)
            self.objects.extend(objects)
            self.diagnostics[path] = errors
            for module, imps in imported.items():
                if module in owners:
                    self.diagnostics[path].append(Diagnostic('error', f'Duplicate module: {module}'))
                owners[module], imports[module] = path, imps
        self.imports=imports
        lookup = {(m, name): oid for m in BUILTIN_MODULES for name, oid in BASE.items()}
        pending = self.objects[:]
        for _ in range(len(pending)+1):
            progressed = False
            for obj in pending[:]:
                identifiers = list(parts(obj.declaration, 'objectIdentifier'))
                if not identifiers:
                    pending.remove(obj)
                    continue
                result = []
                try:
                    for bit in identifiers[-1][1]:
                        if isinstance(bit, int):
                            result.append(bit)
                        elif isinstance(bit, tuple):
                            result.append(int(bit[1]))
                        else:
                            owner = obj.module
                            for mod, symbols in imports.get(obj.module, {}).items():
                                if bit in symbols:
                                    owner = mod
                                    break
                            result.extend(lookup[(owner, bit)])
                    if obj.kind=='TRAP-TYPE':result.extend((0,int(obj.declaration[-1])))
                    obj.oid = '.'.join(map(str, result))
                    lookup[(obj.module, obj.name)] = tuple(result)
                    pending.remove(obj)
                    progressed = True
                except (KeyError, ValueError, TypeError):
                    continue
            if not progressed:
                break
        for module, imps in imports.items():
            for dependency in imps:
                if dependency not in owners and dependency not in BUILTIN_MODULES:
                    self.diagnostics[owners[module]].append(Diagnostic('warning', f'Missing import: {dependency}'))
        for obj in pending:
            self.diagnostics[owners[obj.module]].append(Diagnostic('warning', f'Unresolved OID: {obj.name}', obj.line))
        return self

    def instance_oid(self, obj):
        """Scalar instances use .0; table columns require an actual row index."""
        if not obj.oid:return ''
        accessible=obj.access in ('read-only','read-write','read-create','write-only','accessible-for-notify')
        column=any(parent.kind=='OBJECT-TYPE' and parent.oid and obj.oid.startswith(parent.oid+'.') for parent in self.objects)
        return obj.oid+'.0' if obj.kind=='OBJECT-TYPE' and accessible and not column else obj.oid

    def resolve(self, name):
        raw = name.strip().lstrip('.')
        if re.fullmatch(r'\d+(?:\.\d+)+', raw):
            from .models import oid_tuple
            return '.'.join(map(str, oid_tuple(raw)))
        module, symbol = raw.split('::', 1) if '::' in raw else ('', raw)
        symbol, dot, suffix = symbol.partition('.')
        matches = [x for x in self.objects if x.name == symbol and x.oid and (not module or x.module == module)]
        if len(matches) != 1:
            raise ValueError('OID name is missing or ambiguous; use MODULE::name.instance')
        result = matches[0].oid + ('.'+suffix if dot else '')
        from .models import oid_tuple
        oid_tuple(result)
        return result

    def describe(self, oid):
        candidates = [x for x in self.objects if x.oid and (oid == x.oid or oid.startswith(x.oid+'.'))]
        return max(candidates, key=lambda x: len(x.oid)) if candidates else None

    def describe_instance(self,oid):
        """Resolve an instance without treating every descendant of a scalar as documented."""
        obj=self.describe(oid)
        if not obj or obj.kind!='OBJECT-TYPE':return None
        expected=self.instance_oid(obj)
        if expected!=obj.oid:return obj if oid in (obj.oid,expected) else None
        if obj.access in ('read-only','read-write','read-create','write-only','accessible-for-notify') and oid.startswith(obj.oid+'.'):return obj
        return None
