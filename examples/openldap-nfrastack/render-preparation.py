#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
#
# SPDX-License-Identifier: 0BSD

"""Render isolated migration storage and preparation inputs; never access a cluster."""

import json
from pathlib import Path
root=Path(__file__).resolve().parents[2]
example=root/'examples/openldap-nfrastack'
image='docker.io/nfrastack/openldap:2.6-8.1.1@sha256:5e5c8fdd1d7e3a9623500839699b258d0569bf965528b85c682ec5d0902f5d6c'
name='openldap-nfrastack-stage'
meta={'name':name,'namespace':'identity','labels':{'migration':name}}
security={'runAsNonRoot':True,'runAsUser':389,'runAsGroup':389,'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}}
config={p:(example/p).read_text() for p in ('prepare.sh','prepare.conf')}
config['oauth.schema.conf']=(root/'kubernetes/apps/identity/openldap/app/oauth.schema.conf').read_text()
mounts=[{'name':'data','mountPath':'/data'},{'name':'data','mountPath':'/var/lib/openldap','subPath':'db'},{'name':'data','mountPath':'/restore','subPath':'source','readOnly':True},{'name':'run','mountPath':'/run/openldap'},{'name':'source','mountPath':'/source','readOnly':True},{'name':'tls','mountPath':'/certs','readOnly':True},{'name':'admin','mountPath':'/secrets','readOnly':True}]
items=[{'apiVersion':'v1','kind':'PersistentVolumeClaim','metadata':meta,'spec':{'accessModes':['ReadWriteOnce'],'storageClassName':'openebs-zfs','resources':{'requests':{'storage':'5Gi'}}}}, {'apiVersion':'v1','kind':'ConfigMap','metadata':meta,'data':config}, {'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':meta,'spec':{'podSelector':{'matchLabels':{'migration':name}},'policyTypes':['Ingress','Egress'],'ingress':[],'egress':[]}}, {'apiVersion':'v1','kind':'Pod','metadata':meta,'spec':{'automountServiceAccountToken':False,'hostAliases':[{'ip':'127.0.0.1','hostnames':['ldap.finnes.dev']}],'securityContext':{'fsGroup':389,'seccompProfile':{'type':'RuntimeDefault'}},'initContainers':[{'name':'directories','image':image,'securityContext':security,'command':['sh','-ec','mkdir -p /data/db /data/source /data/artifacts /run/openldap/run'],'volumeMounts':[{'name':'data','mountPath':'/data'},{'name':'run','mountPath':'/run/openldap'}]}],'containers':[{'name':'ldap','image':image,'securityContext':security,'command':['sh','-ec','sleep infinity'],'volumeMounts':mounts,'resources':{'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'memory':'1Gi'}}}],'volumes':[{'name':'data','persistentVolumeClaim':{'claimName':name}},{'name':'run','emptyDir':{}},{'name':'source','configMap':{'name':name}},{'name':'tls','secret':{'secretName':'openldap-tls','defaultMode':0o440}},{'name':'admin','secret':{'secretName':'openldap','defaultMode':0o440,'items':[{'key':'LDAP_ROOT_PASSWORD','path':'admin'}]}}]}}]
print(json.dumps({'apiVersion':'v1','kind':'List','items':items},indent=2))
