'use strict';

const fs = require('node:fs');
const path = require('node:path');

// Ship the tar.gz user-local installer and the shared uninstaller inside the
// packaged app, so the deb gets them under /opt and the tarball at its root.
module.exports = async context => {
  if (context.electronPlatformName !== 'linux') return;
  const source = path.join(__dirname, 'linux');
  for (const name of ['install.sh', 'uninstall.sh']) {
    const target = path.join(context.appOutDir, name);
    fs.copyFileSync(path.join(source, name), target);
    fs.chmodSync(target, 0o755);
  }
};
