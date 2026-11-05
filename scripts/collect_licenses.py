# SPDX-License-Identifier: Apache-2.0
from importlib.metadata import distributions
from pathlib import Path
import shutil


def collect(target):
    target=Path(target);target.mkdir(parents=True,exist_ok=True)
    for distribution in distributions():
        name=distribution.metadata.get('Name','unknown')
        for file in distribution.files or []:
            if any(token in file.name.lower() for token in ('license','copying','notice')) and not str(file).endswith(('.py','.pyc')):
                source=Path(distribution.locate_file(file))
                if source.is_file():
                    destination=target/name/str(file).replace('../','_up_/')
                    destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
    return target
