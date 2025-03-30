# 👩‍💻 Infra 👩‍💻

### MicroOS k3s-server with SELinux

```bash
transactional-update setup-selinux
# should be included by default transactional-update pkg container-selinux selinux-policy-base
systemctl reboot
transactional-update pkg install k3s-selinux policycoreutils-python-utils
systemctl reboot
vim /etc/systemd/system/k3s.service
# add: --selinux --kube-controller-arg=flex-volume-plugin-dir=/var/lib/rancher/k3s/agent/libexec/kubernetes
semanage fcontext -a -t container_runtime_exec_t "/var/lib/rancher/k3s/agent/libexec(/.*)?"
restorecon -Rv /var/lib/rancher/k3s/agent/libexec
systemctl daemon-reload
systemctl restart k3s
```
