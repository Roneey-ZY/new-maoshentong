/* Trusted, local code only. Never interpolate model text, user fields or credentials. */
(() => {
  document.documentElement.lang = 'zh-CN';
  const root = document.querySelector('[data-tc-shell]');
  if (!root || !window.gsap) return;
  document.documentElement.dataset.ready = '1';
  const previous = window.__tradecheckMotion;
  if (previous && previous.root === root && root.isConnected) return;
  if (previous) previous.destroy();
  const gsap = window.gsap;
  const contexts = [];
  const listeners = [];
  let observer;
  let disposed = false;
  let mm = gsap.matchMedia();
  const cleanup = () => {
    if (disposed) return;
    disposed = true;
    observer?.disconnect();
    listeners.forEach(([target, type, handler]) => target.removeEventListener(type, handler));
    mm.revert();
    contexts.forEach(context => context.revert());
    if (window.__tradecheckMotion?.root === root) delete window.__tradecheckMotion;
  };
  const onceKey = 'tradecheck-intro-v1-' + (root.dataset.view || 'workbench');
  let first = true;
  try { first = sessionStorage.getItem(onceKey) !== 'seen'; sessionStorage.setItem(onceKey, 'seen'); } catch (_) { /* Storage is optional. */ }
  mm.add('(prefers-reduced-motion: no-preference)', () => {
    const context = gsap.context((self) => {
      if (first) {
        gsap.timeline({defaults:{duration:.44,ease:'power2.out'}})
          .from(root.querySelector('.tc-title'),{y:8,opacity:.5},0)
          .from(root.querySelectorAll('.tc-document'),{x:7,opacity:.65,stagger:.065},.08)
          .from(root.querySelector('.tc-document-track span'),{scaleX:0,duration:.65},.18);
      }
      // One finite evidence-route replay on intent; no running loop or timers.
      const board = root.querySelector('.tc-documents');
      if (board) {
        self.add('showRoute', () => {
          gsap.fromTo(board.querySelector('.tc-document-track span'),{scaleX:0},{scaleX:1,duration:.6,ease:'power2.out',overwrite:'auto'});
        });
        board.addEventListener('pointerenter', self.showRoute);
        listeners.push([board,'pointerenter',self.showRoute]);
      }
      if (window.ScrollTrigger) {
        gsap.registerPlugin(window.ScrollTrigger);
        const scroller = document.querySelector('[data-testid="stMain"]');
        const targets = ['tc-prepare','tc-run','tc-results'].map(id => document.getElementById(id)).filter(Boolean);
        // Scroll changes only a navigation state. It never pins or hides a table,
        // moves document fields, replaces scrolling, or creates a fake audit state.
        targets.forEach((target,index) => window.ScrollTrigger.create({
          trigger:target,scroller:scroller || undefined,start:'top 30%',end:'max',
          onEnter:() => setReadingStep(index),onLeaveBack:() => setReadingStep(Math.max(0,index-1))
        }));
      }
    },root);
    contexts.push(context);
    return () => {
      context.revert();
      listeners.splice(0).forEach(([target,type,handler]) => target.removeEventListener(type,handler));
    };
  });
  function setReadingStep(index) {
    root.querySelectorAll('.tc-step').forEach((step,i) => step.classList.toggle('is-reading',i===index));
  }
  // Streamlit can replace a fragment on rerun. Stop its contexts on removal;
  // this observer only checks connectivity and never reads form/document data.
  observer = new MutationObserver(() => { if (!root.isConnected) cleanup(); });
  observer.observe(document.body,{childList:true,subtree:true});
  const pagehide = () => cleanup();
  window.addEventListener('pagehide',pagehide,{once:true});
  listeners.push([window,'pagehide',pagehide]);
  window.__tradecheckMotion = {root,destroy:cleanup};
})();
