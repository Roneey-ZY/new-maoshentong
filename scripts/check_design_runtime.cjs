/* Exercise the actual Python-rendered loaders with an existing host AMD loader. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const bundle = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
let amdCalls = 0;
const define = () => {amdCalls++;};
define.amd = {};
const hostModule = {exports:{host:true}};
const sandbox = {window:{},self:{},console,setTimeout,clearTimeout,define,module:hostModule,exports:hostModule.exports};
// A real Window exposes window.window through a getter. ES modules run strict:
// assigning to that getter (as classic UMD does) throws unless this is a facade.
Object.defineProperty(sandbox.window,'window',{get(){return this;}});
vm.createContext(sandbox);
for(let rerun=0;rerun<2;rerun++) {
  for(const loader of bundle.loaders) vm.runInContext("'use strict';\n"+loader,sandbox,{timeout:5000});
  assert.equal(sandbox.window.gsap.version,'3.14.2');
  assert.equal(sandbox.window.ScrollTrigger.version,'3.14.2');
  assert.equal(sandbox.define,define);
  assert.equal(sandbox.module,hostModule);
  assert.equal(amdCalls,0);
}
sandbox.window.gsap.ticker.sleep();
console.log('Actual design loaders: GSAP + ScrollTrigger 3.14.2; AMD/CommonJS globals preserved; rerun safe.');
