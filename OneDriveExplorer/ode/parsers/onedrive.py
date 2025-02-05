# OneDriveExplorer
# Copyright (C) 2022
#
# This file is part of OneDriveExplorer
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#

import os
import hashlib
import logging
import pandas as pd
from Registry import Registry
import ode.parsers.recbin

log = logging.getLogger(__name__)

find_deleted = ode.parsers.recbin.DeleteProcessor()


class OneDriveParser:
    def __init__(self):
        pass

    def hash_file(self, file):
        BUF_SIZE = 65536
        sha1 = hashlib.sha1()

        try:
            with open(file, 'rb') as f:
                while True:
                    data = f.read(BUF_SIZE)
                    if not data:
                        break
                    sha1.update(data)

            return sha1.hexdigest()
        except Exception:
            return ''

    def parse_reg(self, reghive, account, df):
        reg_handle = Registry.Registry(reghive)
        int_keys = reg_handle.open('SOFTWARE\\SyncEngines\\Providers\\OneDrive')
        od_keys = reg_handle.open(f'SOFTWARE\\Microsoft\\OneDrive\\Accounts\\{account}\\Tenants')
        ac_keys = reg_handle.open('SOFTWARE\\Microsoft\\OneDrive\\Accounts')

        df['MountPoint'] = ''
        for providers in int_keys.subkeys():
            df.loc[(df.resourceID == providers.name()), ['MountPoint']] = [x.value() for x in list(providers.values()) if x.name() == 'MountPoint'][0]
            df.loc[(df.scopeID == providers.name()), ['MountPoint']] = [x.value() for x in list(providers.values()) if x.name() == 'MountPoint'][0]

        for x in [value for subkey in [acc2.values() for acc in ac_keys.subkeys() for acc2 in acc.subkeys() if acc2.name() == 'ScopeIdToMountPointPathCache'] for value in subkey]:
            if x.value() is not None:
                df.loc[df['resourceID'] == x.name(), 'MountPoint'] = x.value()
                df.loc[df['scopeID'] == x.name(), 'MountPoint'] = x.value()

        try:
            reghive.seek(0)
        except Exception:
            pass

        return df, od_keys

    def find_parent(self, x, id_name_dict, parent_dict):
        value = parent_dict.get(x, None)
        if value is None:
            return ''
        else:
            # In case there is a id without name.
            if id_name_dict.get(value, None) is None:
                return self.find_parent(value, id_name_dict, parent_dict) + x

        return self.find_parent(value, id_name_dict, parent_dict) + "\\\\" + str(id_name_dict.get(value))

    # Generate scopeID list instead of passing
    def parse_onedrive(self, df, df_scope, df_GraphMetadata_Records, scopeID, file_path, rbin_df, account=False, reghive=False, recbin=False, localHashAlgorithm=False, gui=False, pb=False, value_label=False):

        allowed_keys = ['scopeID', 'siteID', 'webID', 'listID', 'tenantID', 'webURL', 'remotePath', 'MountPoint', 'spoPermissions', 'shortcutVolumeID', 'shortcutItemIndex']

        df_scope['shortcutVolumeID'] = df_scope['shortcutVolumeID'].apply(lambda x: '{:08x}'.format(x) if pd.notna(x) else '')
        df_scope['shortcutVolumeID'] = df_scope['shortcutVolumeID'].apply(lambda x: '{}{}{}{}-{}{}{}{}'.format(*x.upper()) if x else '')

        if os.path.isdir(file_path):
            directory = file_path
            filename = ['SyncEngineDatabase.db', 'SafeDelete.db']
            h = []
            for f in filename:
                h.append(self.hash_file(f'{file_path}\{f}'))
            hash = h
        else:
            directory, filename = os.path.split(file_path)
            hash = self.hash_file(file_path)

        if reghive:
            try:
                df, od_keys = self.parse_reg(reghive, account, df)

                if gui:
                    pb.stop()
                    pb.configure(mode='indeterminate')
                    value_label['text'] = 'Building folder list. Please wait....'
                    pb.start()

            except Exception as e:
                reghive = False
                log.warning(f'Unable to read registry hive! {e}')
                pass

        try:
            df['MountPoint'] = df['MountPoint'].where(pd.notna(df['MountPoint']), '')
        except KeyError:
            df['MountPoint'] = ''

        id_name_dict = {
            resource_id if resource_id is not None else df.at[index, 'scopeID']:
                df.at[index, 'MountPoint'] if name is None else name if name is not None else ''
            for resource_id, name, index in zip(df['resourceID'], df['Name'], df.index)
        }

        parent_dict = {resource_id if resource_id is not None else df.at[index, 'scopeID']: '' if parent_id is None else parent_id
                       for resource_id, parent_id, index in zip(df['resourceID'], df['parentResourceID'], df.index)}

        if 'Path' in df.columns:
            df['Level'] = df['Path'].str.split('\\\\').str.len()
            convert = {'fileStatus': 'Int64',
                       'volumeID': 'Int64',
                       'sharedItem': 'Int64',
                       'folderStatus': 'Int64',
                       'shortcutVolumeID': 'Int64',
                       'shortcutItemIndex': 'Int64'
                       }

        else:
            df['Path'] = df.resourceID.apply(lambda x: self.find_parent(x, id_name_dict, parent_dict).lstrip('\\\\').split('\\\\'))
            df['Level'] = df['Path'].str.len()
            df['Path'] = df['Path'].str.join('\\')
            convert = {'fileStatus': 'Int64',
                       'volumeID': 'Int64',
                       'itemIndex': 'Int64',
                       'sharedItem': 'Int64',
                       'folderStatus': 'Int64',
                       'shortcutVolumeID': 'Int64',
                       'shortcutItemIndex': 'Int64'
                       }

        parent_resource_dict = df[(df['resourceID'].notnull()) & (df['Type'] == 'Folder')].set_index('resourceID').apply(lambda x: x['Path'] + '\\' + x['Name'], axis=1).to_dict()

        for index, row in rbin_df.iterrows():
            parent_resource_id = row['parentResourceId']
            if parent_resource_id in parent_resource_dict:
                rbin_df.at[index, 'Path'] = parent_resource_dict[parent_resource_id]

        if reghive and recbin:
            rbin = find_deleted.find_deleted(recbin, od_keys, localHashAlgorithm, rbin_df, gui=gui, pb=pb, value_label=value_label)
            lrbin_df = pd.DataFrame.from_records(rbin)
            rbin_df = pd.concat([rbin_df, lrbin_df], ignore_index=True, axis=0)

        df['FileSort'] = ''
        df['FolderSort'] = ''

        df.loc[df.Type == 'File', ['FileSort']] = df['Name'].str.lower()
        df.loc[df.Type == 'Folder', ['FolderSort']] = df['Name'].str.lower()

        df = df.astype(convert)
        df['volumeID'].fillna(0, inplace=True)
        df['itemIndex'].fillna(0, inplace=True)
        df['shortcutVolumeID'].fillna(0, inplace=True)
        df['shortcutItemIndex'].fillna(0, inplace=True)

        df['volumeID'] = df['volumeID'].apply(lambda x: '{:08x}'.format(x) if pd.notna(x) else '')
        df['volumeID'] = df['volumeID'].apply(lambda x: '{}{}{}{}-{}{}{}{}'.format(*x.upper()) if x else '')
        df['shortcutVolumeID'] = df['shortcutVolumeID'].apply(lambda x: '{:08x}'.format(x) if pd.notna(x) else '')
        df['shortcutVolumeID'] = df['shortcutVolumeID'].apply(lambda x: '{}{}{}{}-{}{}{}{}'.format(*x.upper()) if x else '')

        cache = {}
        final = []
        is_del = []

        if not df_GraphMetadata_Records.empty:
            df_GraphMetadata_Records.set_index('resourceID', inplace=True)

        for row in df.sort_values(
            by=['Level', 'parentResourceID', 'Type', 'FileSort', 'FolderSort', 'libraryType'],
                ascending=[False, False, False, True, False, False]).to_dict('records'):
            if row['Type'] == 'File':
                try:
                    if 'diskCreationTime' in row:
                        file = {key: row[key] for key in ('parentResourceID', 'resourceID', 'eTag', 'Path', 'Name', 'fileStatus', 'lastHydrationType', 'lastKnownPinState','spoPermissions', 'volumeID', 'itemIndex', 'diskLastAccessTime', 'diskCreationTime', 'lastChange', 'firstHydrationTime', 'lastHydrationTime', 'hydrationCount', 'size', 'localHashDigest', 'sharedItem', 'Media')}

                    elif 'diskLastAccessTime' in row:
                        file = {key: row[key] for key in ('parentResourceID', 'resourceID', 'eTag', 'Path', 'Name', 'fileStatus', 'lastHydrationType', 'spoPermissions', 'volumeID', 'itemIndex', 'diskLastAccessTime','lastChange', 'firstHydrationTime', 'lastHydrationTime', 'hydrationCount', 'size', 'localHashDigest', 'sharedItem', 'Media')}

                    elif 'hydrationCount' in row:
                        file = {key: row[key] for key in ('parentResourceID', 'resourceID', 'eTag', 'Path', 'Name', 'fileStatus', 'lastHydrationType', 'spoPermissions', 'volumeID', 'itemIndex', 'lastChange', 'firstHydrationTime', 'lastHydrationTime', 'hydrationCount', 'size', 'localHashDigest', 'sharedItem', 'Media')}

                    elif 'HydrationTime' in row:
                        file = {key: row[key] for key in ('parentResourceID', 'resourceID', 'eTag', 'Path', 'Name', 'fileStatus', 'spoPermissions', 'volumeID', 'itemIndex', 'lastChange', 'HydrationTime', 'size', 'localHashDigest', 'sharedItem', 'Media')}

                    else:
                        file = {key: row[key] for key in ('parentResourceID', 'resourceID', 'eTag', 'Path', 'Name', 'fileStatus', 'spoPermissions', 'volumeID', 'itemIndex', 'lastChange', 'size', 'localHashDigest', 'sharedItem', 'Media')}

                except Exception as e:
                    if gui:
                        log.error(f'Unable to read dataframe. Something went wrong. {e}')
                    else:
                        print(f'Unable to read dataframe. Something went wrong. {e}')
                    return {}, rbin_df

                file.setdefault('Metadata', '')

                try:
                    metadata = df_GraphMetadata_Records.loc[row['resourceID']].to_dict()

                except Exception:
                    metadata = None

                if metadata:
                    file['Metadata'] = metadata

                folder = cache.setdefault(row['parentResourceID'], {})
                folder.setdefault('Files', []).append(file)
            else:
                if 'Scope' in row['Type']:
                    if row['scopeID'] not in scopeID:
                        continue
                    scope = {key: row[key] for key in row if key in allowed_keys}
                    folder = cache.get(row['scopeID'], {})
                    temp = {**scope, **folder}
                    final.insert(0, temp)
                else:
                    if 'folderColor' in row:
                        sub_folder = {key: row[key] for key in (
                                      'parentResourceID', 'resourceID', 'eTag', 'Path', 'Name', 'folderStatus', 'spoPermissions', 'volumeID',
                                      'itemIndex', 'sharedItem', 'folderColor')}
                    else:
                        sub_folder = {key: row[key] for key in (
                                      'parentResourceID', 'resourceID', 'eTag', 'Path', 'Name', 'folderStatus', 'spoPermissions', 'volumeID',
                                      'itemIndex', 'sharedItem')}
                    if row['resourceID'] in scopeID:
                        scopeID.remove(row['resourceID'])
                        for s in df_scope.loc[df_scope['scopeID'] == row['resourceID']].to_dict('records'):
                            scope = {key: s[key] for key in s if key in allowed_keys}
                            scope['MountPoint'] = row['MountPoint']
                            scope['spoPermissions'] = s['spoPermissions']
                            scope['shortcutVolumeID'] = s['shortcutVolumeID']
                            scope['shortcutItemIndex'] = s['shortcutItemIndex']
                        folder = cache.get(row['resourceID'], {})
                        temp = {**sub_folder, **folder}
                        scope.setdefault('Links', []).append(temp)
                        folder_merge = cache.setdefault(row['parentResourceID'], {})
                        folder_merge.setdefault('Scope', []).append(scope)
                    else:
                        folder = cache.get(row['resourceID'], {})
                        temp = {**sub_folder, **folder}
                        folder_merge = cache.setdefault(row['parentResourceID'], {})
                        folder_merge.setdefault('Folders', []).append(temp)

        if not rbin_df.empty:
            for row in rbin_df.to_dict('records'):
                file = {key: row[key] for key in ('parentResourceId', 'resourceId', 'eTag', 'Path', 'Name', 'inRecycleBin', 'volumeId', 'fileId', 'DeleteTimeStamp', 'notificationTime', 'size', 'hash', 'deletingProcess')}

                # Nesting of deleted items
                # dfolder = dcache.setdefault(row['parentResourceId'], {})
                # dfolder.setdefault('Files', []).append(file)

                is_del.append(file)

            deleted = {'Type': 'Root Deleted',
                       'Children': ''
                       }

            deleted['Children'] = is_del
            final.append(deleted)

        cache = {"Path": directory,
                 "Name": filename,
                 "Hash": hash,
                 "Data": ''
                 }

        cache['Data'] = final

        df_GraphMetadata_Records.reset_index(inplace=True)
        try:
            df_GraphMetadata_Records.drop('index', axis=1, inplace=True)
        except Exception:
            pass

        return cache, rbin_df
