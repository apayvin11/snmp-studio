# SPDX-License-Identifier: Apache-2.0
"""Session-local observations; imported MIB definitions remain unchanged."""
from dataclasses import dataclass,field
from .models import oid_tuple


def within(oid,root):
    address,prefix=oid_tuple(oid),oid_tuple(root)
    return address[:len(prefix)]==prefix


@dataclass
class DeviceObservations:
    values: dict=field(default_factory=dict)
    missing: set=field(default_factory=set)
    scans: list=field(default_factory=list)
    present_prefixes: set=field(default_factory=set)

    def record(self,operation,root,values):
        seen={v.oid for v in values if not v.error}
        full=operation=='WALK_ALL'
        if operation in ('WALK','WALK_ALL'):
            for oid in self.values:
                if (full or within(oid,root)) and oid not in seen:self.missing.add(oid)
            self.scans=[scan for scan in self.scans if not full and not (not scan[1] and within(scan[0],root))]
            self.scans.append((root,full,seen));self.scans=self.scans[-16:]
        for value in values:
            if not value.error:
                self.values[value.oid]=value;self.missing.discard(value.oid)
            elif value.error in ('NoSuchObject','NoSuchInstance','noSuchName'):
                self.missing.add(value.oid)
        while len(self.values)>50000:
            oid=next(iter(self.values));self.values.pop(oid);self.missing.discard(oid)

        self.present_prefixes.clear()
        for oid in self.values:
            if oid in self.missing:continue
            prefix=oid
            while '.' in prefix:
                prefix=prefix.rsplit('.',1)[0];self.present_prefixes.add(prefix)

    def object_status(self,obj,index):
        if obj.kind!='OBJECT-TYPE' or not obj.oid:return ''
        expected=index.instance_oid(obj)
        if expected!=obj.oid:
            if expected in self.missing:return 'missing'
            if expected in self.values or obj.oid in self.values and obj.oid not in self.missing:return 'present'
            for root,full,seen in reversed(self.scans):
                if full or within(expected,root):return 'present' if expected in seen else 'missing'
        else:
            if obj.oid in self.present_prefixes:return 'present'
            for root,full,seen in reversed(self.scans):
                if full or within(obj.oid,root):return 'missing'
        return ''
