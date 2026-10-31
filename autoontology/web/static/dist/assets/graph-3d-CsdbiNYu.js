import{F as e,I as t,P as n,R as r}from"./labels-BSF6cR1f.js";import{F as i,S as a,_ as o,a as s,c,d as l,f as u,g as d,h as f,i as p,l as m,m as h,n as g,o as _,p as v,r as y,s as ee,t as b,u as x,v as S,x as C}from"./types-CMW9xC9d.js";import{A as w,B as T,C as E,D,E as O,F as k,I as A,L as j,M,N,O as P,P as te,R as ne,S as re,T as ie,V as ae,_ as oe,a as se,b as ce,c as le,d as ue,f as de,g as fe,h as pe,i as F,j as me,k as he,l as ge,m as _e,n as ve,o as ye,p as be,r as xe,s as Se,t as Ce,u as we,v as Te,w as Ee,x as De,y as Oe,z as ke}from"./three-VxN7TsVy.js";import{n as Ae,r as je}from"./graph-BGfrgC_N.js";var I=r(e(),1);function Me(e){Pe(e);let t=Ne(e);return e.on=t.on,e.off=t.off,e.fire=t.fire,e}function Ne(e){let t=Object.create(null);return{on:function(n,r,i){if(typeof r!=`function`)throw Error(`callback is expected to be a function`);let a=t[n];return a||=t[n]=[],a.push({callback:r,ctx:i}),e},off:function(n,r){if(n===void 0)return t=Object.create(null),e;if(t[n]){if(typeof r!=`function`)delete t[n];else{let e=t[n];for(let t=0;t<e.length;++t)e[t].callback===r&&e.splice(t,1)}}return e},fire:function(n){let r=t[n];if(!r)return e;let i;arguments.length>1&&(i=Array.prototype.slice.call(arguments,1));for(let e=0;e<r.length;++e){let t=r[e];t.callback.apply(t.ctx,i)}return e}}}function Pe(e){if(!e)throw Error(`Eventify cannot use falsy object as events subject`);let t=[`on`,`fire`,`off`];for(let n=0;n<t.length;++n)if(e.hasOwnProperty(t[n]))throw Error(`Subject cannot be eventified, since it already has property '`+t[n]+`'`)}var Fe=Ie;function Ie(e){if(e||={},`uniqueLinkId`in e&&(console.warn("ngraph.graph: Starting from version 0.14 `uniqueLinkId` is deprecated.\nUse `multigraph` option instead\n",`
`,`Note: there is also change in default behavior: From now on each graph
is considered to be not a multigraph by default (each edge is unique).`),e.multigraph=e.uniqueLinkId),e.multigraph===void 0&&(e.multigraph=!1),typeof Map!=`function`)throw Error("ngraph.graph requires `Map` to be defined. Please polyfill it before using ngraph");var t=new Map,n=new Map,r={},i=0,a=e.multigraph?ee:y,o=[],s=M,c=M,l=M,u=M,d={version:20,addNode:h,addLink:v,removeLink:C,removeNode:_,getNode:g,getNodeCount:b,getLinkCount:x,getEdgeCount:x,getLinksCount:x,getNodesCount:b,getLinks:S,forEachNode:te,forEachLinkedNode:k,forEachLink:O,beginUpdate:l,endUpdate:u,clear:D,hasLink:T,hasNode:g,getLink:T,getLinkById:E};return Me(d),f(),d;function f(){var e=d.on;d.on=t;function t(){return d.beginUpdate=l=N,d.endUpdate=u=P,s=p,c=m,d.on=e,e.apply(d,arguments)}}function p(e,t){o.push({link:e,changeType:t})}function m(e,t){o.push({node:e,changeType:t})}function h(e,n){if(e===void 0)throw Error(`Invalid node identifier`);l();var r=g(e);return r?(r.data=n,c(r,`update`)):(r=new Le(e,n),c(r,`add`)),t.set(e,r),u(),r}function g(e){return t.get(e)}function _(e){var n=g(e);if(!n)return!1;l();var r=n.links;return r&&(r.forEach(w),n.links=null),t.delete(e),c(n,`remove`),u(),!0}function v(e,t,r){l();var i=g(e)||h(e),o=g(t)||h(t),c=a(e,t,r),d=n.has(c.id);return n.set(c.id,c),Re(i,c),e!==t&&Re(o,c),s(c,d?`update`:`add`),u(),c}function y(e,t,r){var i=Be(e,t),a=n.get(i);return a?(a.data=r,a):new ze(e,t,r,i)}function ee(e,t,n){var i=Be(e,t),a=r.hasOwnProperty(i);if(a||T(e,t)){a||(r[i]=0);var o=`@`+ ++r[i];i=Be(e+o,t+o)}return new ze(e,t,n,i)}function b(){return t.size}function x(){return n.size}function S(e){var t=g(e);return t?t.links:null}function C(e,t){return t!==void 0&&(e=T(e,t)),w(e)}function w(e){if(!e||!n.get(e.id))return!1;l(),n.delete(e.id);var t=g(e.fromId),r=g(e.toId);return t&&t.links.delete(e),r&&r.links.delete(e),s(e,`remove`),u(),!0}function T(e,t){if(e!==void 0&&t!==void 0)return n.get(Be(e,t))}function E(e){if(e!==void 0)return n.get(e)}function D(){l(),te(function(e){_(e.id)}),u()}function O(e){if(typeof e==`function`)for(var t=n.values(),r=t.next();!r.done;){if(e(r.value))return!0;r=t.next()}}function k(e,t,n){var r=g(e);if(r&&r.links&&typeof t==`function`)return n?j(r.links,e,t):A(r.links,e,t)}function A(e,n,r){for(var i,a=e.values(),o=a.next();!o.done;){var s=o.value,c=s.fromId===n?s.toId:s.fromId;if(i=r(t.get(c),s),i)return!0;o=a.next()}}function j(e,n,r){for(var i,a=e.values(),o=a.next();!o.done;){var s=o.value;if(s.fromId===n&&(i=r(t.get(s.toId),s),i))return!0;o=a.next()}}function M(){}function N(){i+=1}function P(){--i,i===0&&o.length>0&&(d.fire(`changed`,o),o.length=0)}function te(e){if(typeof e!=`function`)throw Error(`Function is expected to iterate over graph nodes. You passed `+e);for(var n=t.values(),r=n.next();!r.done;){if(e(r.value))return!0;r=n.next()}}}function Le(e,t){this.id=e,this.links=null,this.data=t}function Re(e,t){e.links?e.links.add(t):e.links=new Set([t])}function ze(e,t,n,r){this.fromId=e,this.toId=t,this.data=n,this.id=r}function Be(e,t){return e.toString()+`👉 `+t.toString()}var Ve=t(((e,t)=>{t.exports=function(e){return e===0?`x`:e===1?`y`:e===2?`z`:`c`+(e+1)}})),L=t(((e,t)=>{var n=Ve();t.exports=function(e){return t;function t(t,r){let i=r&&r.indent||0,a=r&&r.join!==void 0?r.join:`
`,o=Array(i+1).join(` `),s=[];for(let r=0;r<e;++r){let e=n(r),i=r===0?``:o;s.push(i+t.replace(/{var}/g,e))}return s.join(a)}}})),He=t(((e,t)=>{var n=L();t.exports=r,t.exports.generateCreateBodyFunctionBody=i,t.exports.getVectorCode=o,t.exports.getBodyCode=a;function r(e,t){let n=i(e,t),{Body:r}=Function(n)();return r}function i(e,t){return`
${o(e,t)}
${a(e,t)}
return {Body: Body, Vector: Vector};
`}function a(e){let t=n(e),r=t(`{var}`,{join:`, `});return`
function Body(${r}) {
  this.isPinned = false;
  this.pos = new Vector(${r});
  this.force = new Vector();
  this.velocity = new Vector();
  this.mass = 1;

  this.springCount = 0;
  this.springLength = 0;
}

Body.prototype.reset = function() {
  this.force.reset();
  this.springCount = 0;
  this.springLength = 0;
}

Body.prototype.setPosition = function (${r}) {
  ${t(`this.pos.{var} = {var} || 0;`,{indent:2})}
};`}function o(e,t){let r=n(e),i=``;return t&&(i=`${r(`
   var v{var};
Object.defineProperty(this, '{var}', {
  set: function(v) { 
    if (!Number.isFinite(v)) throw new Error('Cannot set non-numbers to {var}');
    v{var} = v; 
  },
  get: function() { return v{var}; }
});`)}`),`function Vector(${r(`{var}`,{join:`, `})}) {
  ${i}
    if (typeof arguments[0] === 'object') {
      // could be another vector
      let v = arguments[0];
      ${r(`if (!Number.isFinite(v.{var})) throw new Error("Expected value is not a finite number at Vector constructor ({var})");`,{indent:4})}
      ${r(`this.{var} = v.{var};`,{indent:4})}
    } else {
      ${r(`this.{var} = typeof {var} === "number" ? {var} : 0;`,{indent:4})}
    }
  }
  
  Vector.prototype.reset = function () {
    ${r(`this.{var} = `,{join:``})}0;
  };`}})),Ue=t(((e,t)=>{var n=L(),r=Ve();t.exports=i,t.exports.generateQuadTreeFunctionBody=a,t.exports.getInsertStackCode=u,t.exports.getQuadNodeCode=l,t.exports.isSamePosition=o,t.exports.getChildBodyCode=c,t.exports.setChildBodyCode=s;function i(e){let t=a(e);return Function(t)()}function a(e){let t=n(e),i=2**e;return`
${u()}
${l(e)}
${o(e)}
${c(e)}
${s(e)}

function createQuadTree(options, random) {
  options = options || {};
  options.gravity = typeof options.gravity === 'number' ? options.gravity : -1;
  options.theta = typeof options.theta === 'number' ? options.theta : 0.8;

  var gravity = options.gravity;
  var updateQueue = [];
  var insertStack = new InsertStack();
  var theta = options.theta;

  var nodesCache = [];
  var currentInCache = 0;
  var root = newNode();

  return {
    insertBodies: insertBodies,

    /**
     * Gets root node if it is present
     */
    getRoot: function() {
      return root;
    },

    updateBodyForce: update,

    options: function(newOptions) {
      if (newOptions) {
        if (typeof newOptions.gravity === 'number') {
          gravity = newOptions.gravity;
        }
        if (typeof newOptions.theta === 'number') {
          theta = newOptions.theta;
        }

        return this;
      }

      return {
        gravity: gravity,
        theta: theta
      };
    }
  };

  function newNode() {
    // To avoid pressure on GC we reuse nodes.
    var node = nodesCache[currentInCache];
    if (node) {
${f(`      node.`)}
      node.body = null;
      node.mass = ${t(`node.mass_{var} = `,{join:``})}0;
      ${t(`node.min_{var} = node.max_{var} = `,{join:``})}0;
    } else {
      node = new QuadNode();
      nodesCache[currentInCache] = node;
    }

    ++currentInCache;
    return node;
  }

  function update(sourceBody) {
    var queue = updateQueue;
    var v;
    ${t(`var d{var};`,{indent:4})}
    var r; 
    ${t(`var f{var} = 0;`,{indent:4})}
    var queueLength = 1;
    var shiftIdx = 0;
    var pushIdx = 1;

    queue[0] = root;

    while (queueLength) {
      var node = queue[shiftIdx];
      var body = node.body;

      queueLength -= 1;
      shiftIdx += 1;
      var differentBody = (body !== sourceBody);
      if (body && differentBody) {
        // If the current node is a leaf node (and it is not source body),
        // calculate the force exerted by the current node on body, and add this
        // amount to body's net force.
        ${t(`d{var} = body.pos.{var} - sourceBody.pos.{var};`,{indent:8})}
        r = Math.sqrt(${t(`d{var} * d{var}`,{join:` + `})});

        if (r === 0) {
          // Poor man's protection against zero distance.
          ${t(`d{var} = (random.nextDouble() - 0.5) / 50;`,{indent:10})}
          r = Math.sqrt(${t(`d{var} * d{var}`,{join:` + `})});
        }

        // This is standard gravitation force calculation but we divide
        // by r^3 to save two operations when normalizing force vector.
        v = gravity * body.mass * sourceBody.mass / (r * r * r);
        ${t(`f{var} += v * d{var};`,{indent:8})}
      } else if (differentBody) {
        // Otherwise, calculate the ratio s / r,  where s is the width of the region
        // represented by the internal node, and r is the distance between the body
        // and the node's center-of-mass
        ${t(`d{var} = node.mass_{var} / node.mass - sourceBody.pos.{var};`,{indent:8})}
        r = Math.sqrt(${t(`d{var} * d{var}`,{join:` + `})});

        if (r === 0) {
          // Sorry about code duplication. I don't want to create many functions
          // right away. Just want to see performance first.
          ${t(`d{var} = (random.nextDouble() - 0.5) / 50;`,{indent:10})}
          r = Math.sqrt(${t(`d{var} * d{var}`,{join:` + `})});
        }
        // If s / r < θ, treat this internal node as a single body, and calculate the
        // force it exerts on sourceBody, and add this amount to sourceBody's net force.
        if ((node.max_${r(0)} - node.min_${r(0)}) / r < theta) {
          // in the if statement above we consider node's width only
          // because the region was made into square during tree creation.
          // Thus there is no difference between using width or height.
          v = gravity * node.mass * sourceBody.mass / (r * r * r);
          ${t(`f{var} += v * d{var};`,{indent:10})}
        } else {
          // Otherwise, run the procedure recursively on each of the current node's children.

          // I intentionally unfolded this loop, to save several CPU cycles.
${d()}
        }
      }
    }

    ${t(`sourceBody.force.{var} += f{var};`,{indent:4})}
  }

  function insertBodies(bodies) {
    ${t(`var {var}min = Number.MAX_VALUE;`,{indent:4})}
    ${t(`var {var}max = Number.MIN_VALUE;`,{indent:4})}
    var i = bodies.length;

    // To reduce quad tree depth we are looking for exact bounding box of all particles.
    while (i--) {
      var pos = bodies[i].pos;
      ${t(`if (pos.{var} < {var}min) {var}min = pos.{var};`,{indent:6})}
      ${t(`if (pos.{var} > {var}max) {var}max = pos.{var};`,{indent:6})}
    }

    // Makes the bounds square.
    var maxSideLength = -Infinity;
    ${t(`if ({var}max - {var}min > maxSideLength) maxSideLength = {var}max - {var}min ;`,{indent:4})}

    currentInCache = 0;
    root = newNode();
    ${t(`root.min_{var} = {var}min;`,{indent:4})}
    ${t(`root.max_{var} = {var}min + maxSideLength;`,{indent:4})}

    i = bodies.length - 1;
    if (i >= 0) {
      root.body = bodies[i];
    }
    while (i--) {
      insert(bodies[i], root);
    }
  }

  function insert(newBody) {
    insertStack.reset();
    insertStack.push(root, newBody);

    while (!insertStack.isEmpty()) {
      var stackItem = insertStack.pop();
      var node = stackItem.node;
      var body = stackItem.body;

      if (!node.body) {
        // This is internal node. Update the total mass of the node and center-of-mass.
        ${t(`var {var} = body.pos.{var};`,{indent:8})}
        node.mass += body.mass;
        ${t(`node.mass_{var} += body.mass * {var};`,{indent:8})}

        // Recursively insert the body in the appropriate quadrant.
        // But first find the appropriate quadrant.
        var quadIdx = 0; // Assume we are in the 0's quad.
        ${t(`var min_{var} = node.min_{var};`,{indent:8})}
        ${t(`var max_{var} = (min_{var} + node.max_{var}) / 2;`,{indent:8})}

${a(8)}

        var child = getChild(node, quadIdx);

        if (!child) {
          // The node is internal but this quadrant is not taken. Add
          // subnode to it.
          child = newNode();
          ${t(`child.min_{var} = min_{var};`,{indent:10})}
          ${t(`child.max_{var} = max_{var};`,{indent:10})}
          child.body = body;

          setChild(node, quadIdx, child);
        } else {
          // continue searching in this quadrant.
          insertStack.push(child, body);
        }
      } else {
        // We are trying to add to the leaf node.
        // We have to convert current leaf into internal node
        // and continue adding two nodes.
        var oldBody = node.body;
        node.body = null; // internal nodes do not cary bodies

        if (isSamePosition(oldBody.pos, body.pos)) {
          // Prevent infinite subdivision by bumping one node
          // anywhere in this quadrant
          var retriesCount = 3;
          do {
            var offset = random.nextDouble();
            ${t(`var d{var} = (node.max_{var} - node.min_{var}) * offset;`,{indent:12})}

            ${t(`oldBody.pos.{var} = node.min_{var} + d{var};`,{indent:12})}
            retriesCount -= 1;
            // Make sure we don't bump it out of the box. If we do, next iteration should fix it
          } while (retriesCount > 0 && isSamePosition(oldBody.pos, body.pos));

          if (retriesCount === 0 && isSamePosition(oldBody.pos, body.pos)) {
            // This is very bad, we ran out of precision.
            // if we do not return from the method we'll get into
            // infinite loop here. So we sacrifice correctness of layout, and keep the app running
            // Next layout iteration should get larger bounding box in the first step and fix this
            return;
          }
        }
        // Next iteration should subdivide node further.
        insertStack.push(node, oldBody);
        insertStack.push(node, body);
      }
    }
  }
}
return createQuadTree;

`;function a(t){let n=[],i=Array(t+1).join(` `);for(let t=0;t<e;++t)n.push(i+`if (${r(t)} > max_${r(t)}) {`),n.push(i+`  quadIdx = quadIdx + ${2**t};`),n.push(i+`  min_${r(t)} = max_${r(t)};`),n.push(i+`  max_${r(t)} = node.max_${r(t)};`),n.push(i+`}`);return n.join(`
`)}function d(){let e=Array(11).join(` `),t=[];for(let n=0;n<i;++n)t.push(e+`if (node.quad${n}) {`),t.push(e+`  queue[pushIdx] = node.quad${n};`),t.push(e+`  queueLength += 1;`),t.push(e+`  pushIdx += 1;`),t.push(e+`}`);return t.join(`
`)}function f(e){let t=[];for(let n=0;n<i;++n)t.push(`${e}quad${n} = null;`);return t.join(`
`)}}function o(e){let t=n(e);return`
  function isSamePosition(point1, point2) {
    ${t(`var d{var} = Math.abs(point1.{var} - point2.{var});`,{indent:2})}
  
    return ${t(`d{var} < 1e-8`,{join:` && `})};
  }  
`}function s(e){var t=2**e;return`
function setChild(node, idx, child) {
  ${n()}
}`;function n(){let e=[];for(let n=0;n<t;++n){let t=n===0?`  `:`  else `;e.push(`${t}if (idx === ${n}) node.quad${n} = child;`)}return e.join(`
`)}}function c(e){return`function getChild(node, idx) {
${t()}
  return null;
}`;function t(){let t=[],n=2**e;for(let e=0;e<n;++e)t.push(`  if (idx === ${e}) return node.quad${e};`);return t.join(`
`)}}function l(e){let t=n(e),r=2**e;return`
function QuadNode() {
  // body stored inside this node. In quad tree only leaf nodes (by construction)
  // contain bodies:
  this.body = null;

  // Child nodes are stored in quads. Each quad is presented by number:
  // 0 | 1
  // -----
  // 2 | 3
${i(`  this.`)}

  // Total mass of current node
  this.mass = 0;

  // Center of mass coordinates
  ${t(`this.mass_{var} = 0;`,{indent:2})}

  // bounding box coordinates
  ${t(`this.min_{var} = 0;`,{indent:2})}
  ${t(`this.max_{var} = 0;`,{indent:2})}
}
`;function i(e){let t=[];for(let n=0;n<r;++n)t.push(`${e}quad${n} = null;`);return t.join(`
`)}}function u(){return`
/**
 * Our implementation of QuadTree is non-recursive to avoid GC hit
 * This data structure represent stack of elements
 * which we are trying to insert into quad tree.
 */
function InsertStack () {
    this.stack = [];
    this.popIdx = 0;
}

InsertStack.prototype = {
    isEmpty: function() {
        return this.popIdx === 0;
    },
    push: function (node, body) {
        var item = this.stack[this.popIdx];
        if (!item) {
            // we are trying to avoid memory pressure: create new element
            // only when absolutely necessary
            this.stack[this.popIdx] = new InsertStackElement(node, body);
        } else {
            item.node = node;
            item.body = body;
        }
        ++this.popIdx;
    },
    pop: function () {
        if (this.popIdx > 0) {
            return this.stack[--this.popIdx];
        }
    },
    reset: function () {
        this.popIdx = 0;
    }
};

function InsertStackElement(node, body) {
    this.node = node; // QuadTree node
    this.body = body; // physical body which needs to be inserted to node
}
`}})),We=t(((e,t)=>{t.exports=r,t.exports.generateFunctionBody=i;var n=L();function r(e){let t=i(e);return Function(`bodies`,`settings`,`random`,t)}function i(e){let t=n(e);return`
  var boundingBox = {
    ${t(`min_{var}: 0, max_{var}: 0,`,{indent:4})}
  };

  return {
    box: boundingBox,

    update: updateBoundingBox,

    reset: resetBoundingBox,

    getBestNewPosition: function (neighbors) {
      var ${t(`base_{var} = 0`,{join:`, `})};

      if (neighbors.length) {
        for (var i = 0; i < neighbors.length; ++i) {
          let neighborPos = neighbors[i].pos;
          ${t(`base_{var} += neighborPos.{var};`,{indent:10})}
        }

        ${t(`base_{var} /= neighbors.length;`,{indent:8})}
      } else {
        ${t(`base_{var} = (boundingBox.min_{var} + boundingBox.max_{var}) / 2;`,{indent:8})}
      }

      var springLength = settings.springLength;
      return {
        ${t(`{var}: base_{var} + (random.nextDouble() - 0.5) * springLength,`,{indent:8})}
      };
    }
  };

  function updateBoundingBox() {
    var i = bodies.length;
    if (i === 0) return; // No bodies - no borders.

    ${t(`var max_{var} = -Infinity;`,{indent:4})}
    ${t(`var min_{var} = Infinity;`,{indent:4})}

    while(i--) {
      // this is O(n), it could be done faster with quadtree, if we check the root node bounds
      var bodyPos = bodies[i].pos;
      ${t(`if (bodyPos.{var} < min_{var}) min_{var} = bodyPos.{var};`,{indent:6})}
      ${t(`if (bodyPos.{var} > max_{var}) max_{var} = bodyPos.{var};`,{indent:6})}
    }

    ${t(`boundingBox.min_{var} = min_{var};`,{indent:4})}
    ${t(`boundingBox.max_{var} = max_{var};`,{indent:4})}
  }

  function resetBoundingBox() {
    ${t(`boundingBox.min_{var} = boundingBox.max_{var} = 0;`,{indent:4})}
  }
`}})),Ge=t(((e,t)=>{var n=L();t.exports=r,t.exports.generateCreateDragForceFunctionBody=i;function r(e){let t=i(e);return Function(`options`,t)}function i(e){return`
  if (!Number.isFinite(options.dragCoefficient)) throw new Error('dragCoefficient is not a finite number');

  return {
    update: function(body) {
      ${n(e)(`body.force.{var} -= options.dragCoefficient * body.velocity.{var};`,{indent:6})}
    }
  };
`}})),Ke=t(((e,t)=>{var n=L();t.exports=r,t.exports.generateCreateSpringForceFunctionBody=i;function r(e){let t=i(e);return Function(`options`,`random`,t)}function i(e){let t=n(e);return`
  if (!Number.isFinite(options.springCoefficient)) throw new Error('Spring coefficient is not a number');
  if (!Number.isFinite(options.springLength)) throw new Error('Spring length is not a number');

  return {
    /**
     * Updates forces acting on a spring
     */
    update: function (spring) {
      var body1 = spring.from;
      var body2 = spring.to;
      var length = spring.length < 0 ? options.springLength : spring.length;
      ${t(`var d{var} = body2.pos.{var} - body1.pos.{var};`,{indent:6})}
      var r = Math.sqrt(${t(`d{var} * d{var}`,{join:` + `})});

      if (r === 0) {
        ${t(`d{var} = (random.nextDouble() - 0.5) / 50;`,{indent:8})}
        r = Math.sqrt(${t(`d{var} * d{var}`,{join:` + `})});
      }

      var d = r - length;
      var coefficient = ((spring.coefficient > 0) ? spring.coefficient : options.springCoefficient) * d / r;

      ${t(`body1.force.{var} += coefficient * d{var}`,{indent:6})};
      body1.springCount += 1;
      body1.springLength += r;

      ${t(`body2.force.{var} -= coefficient * d{var}`,{indent:6})};
      body2.springCount += 1;
      body2.springLength += r;
    }
  };
`}})),qe=t(((e,t)=>{var n=L();t.exports=r,t.exports.generateIntegratorFunctionBody=i;function r(e){let t=i(e);return Function(`bodies`,`timeStep`,`adaptiveTimeStepWeight`,t)}function i(e){let t=n(e);return`
  var length = bodies.length;
  if (length === 0) return 0;

  ${t(`var d{var} = 0, t{var} = 0;`,{indent:2})}

  for (var i = 0; i < length; ++i) {
    var body = bodies[i];
    if (body.isPinned) continue;

    if (adaptiveTimeStepWeight && body.springCount) {
      timeStep = (adaptiveTimeStepWeight * body.springLength/body.springCount);
    }

    var coeff = timeStep / body.mass;

    ${t(`body.velocity.{var} += coeff * body.force.{var};`,{indent:4})}
    ${t(`var v{var} = body.velocity.{var};`,{indent:4})}
    var v = Math.sqrt(${t(`v{var} * v{var}`,{join:` + `})});

    if (v > 1) {
      // We normalize it so that we move within timeStep range. 
      // for the case when v <= 1 - we let velocity to fade out.
      ${t(`body.velocity.{var} = v{var} / v;`,{indent:6})}
    }

    ${t(`d{var} = timeStep * body.velocity.{var};`,{indent:4})}

    ${t(`body.pos.{var} += d{var};`,{indent:4})}

    ${t(`t{var} += Math.abs(d{var});`,{indent:4})}
  }

  return (${t(`t{var} * t{var}`,{join:` + `})})/length;
`}})),Je=t(((e,t)=>{t.exports=n;function n(e,t,n,r){this.from=e,this.to=t,this.length=n,this.coefficient=r}})),Ye=t(((e,t)=>{t.exports=n;function n(e,t){var r;if(e||={},t){for(r in t)if(t.hasOwnProperty(r)){var i=e.hasOwnProperty(r),a=typeof t[r];!i||typeof e[r]!==a?e[r]=t[r]:a===`object`&&(e[r]=n(e[r],t[r]))}}return e}})),Xe=t(((e,t)=>{function n(e){i(e);let t=r(e);return e.on=t.on,e.off=t.off,e.fire=t.fire,e}function r(e){let t=Object.create(null);return{on:function(n,r,i){if(typeof r!=`function`)throw Error(`callback is expected to be a function`);let a=t[n];return a||=t[n]=[],a.push({callback:r,ctx:i}),e},off:function(n,r){if(typeof n>`u`)return t=Object.create(null),e;if(t[n]){if(typeof r!=`function`)delete t[n];else{let e=t[n];for(let t=0;t<e.length;++t)e[t].callback===r&&e.splice(t,1)}}return e},fire:function(n){let r=t[n];if(!r)return e;let i;arguments.length>1&&(i=Array.prototype.slice.call(arguments,1));for(let e=0;e<r.length;++e){let t=r[e];t.callback.apply(t.ctx,i)}return e}}}function i(e){if(!e)throw Error(`Eventify cannot use falsy object as events subject`);let t=[`on`,`fire`,`off`];for(let n=0;n<t.length;++n)if(e.hasOwnProperty(t[n]))throw Error(`Subject cannot be eventified, since it already has property '`+t[n]+`'`)}t.exports=n})),Ze=t(((e,t)=>{t.exports=n,t.exports.random=n,t.exports.randomIterator=l;function n(e){return new r(typeof e==`number`?e:+new Date)}function r(e){this.seed=e}r.prototype.next=c,r.prototype.nextDouble=s,r.prototype.uniform=s,r.prototype.gaussian=i,r.prototype.random=s;function i(){var e,t,n;do t=this.nextDouble()*2-1,n=this.nextDouble()*2-1,e=t*t+n*n;while(e>=1||e===0);return t*Math.sqrt(-2*Math.log(e)/e)}r.prototype.levy=a;function a(){var e=3/2,t=(o(1+e)*Math.sin(Math.PI*e/2)/(o((1+e)/2)*e*2**((e-1)/2)))**(1/e);return this.gaussian()*t/Math.abs(this.gaussian())**(1/e)}function o(e){return Math.sqrt(2*Math.PI/e)*(1/Math.E*(e+1/(12*e-1/(10*e))))**e}function s(){var e=this.seed;return e=e+2127912214+(e<<12)|0,e=e^3345072700^e>>>19,e=e+374761393+(e<<5)|0,e=e+3550635116^e<<9,e=e+4251993797+(e<<3)|0,e=e^3042594569^e>>>16,this.seed=e,(e&268435455)/268435456}function c(e){return Math.floor(this.nextDouble()*e)}function l(e,t){var r=t||n();if(typeof r.next!=`function`)throw Error(`customRandom does not match expected API: next() function is missing`);return{forEach:a,shuffle:i};function i(){for(var t=e.length-1,n,i;t>0;--t)n=r.next(t+1),i=e[n],e[n]=e[t],e[t]=i;return e}function a(t){for(var n=e.length-1,i,a;n>0;--n)i=r.next(n+1),a=e[i],e[i]=e[n],e[n]=a,t(a);e.length&&t(e[0])}}})),Qe=t(((e,t)=>{t.exports=l;var n=He(),r=Ue(),i=We(),a=Ge(),o=Ke(),s=qe(),c={};function l(e){var t=Je(),l=Ye(),d=Xe();if(e){if(e.springCoeff!==void 0)throw Error(`springCoeff was renamed to springCoefficient`);if(e.dragCoeff!==void 0)throw Error(`dragCoeff was renamed to dragCoefficient`)}e=l(e,{springLength:10,springCoefficient:.8,gravity:-12,theta:.8,dragCoefficient:.9,timeStep:.5,adaptiveTimeStepWeight:0,dimensions:2,debug:!1});var f=c[e.dimensions];if(!f){var p=e.dimensions;f={Body:n(p,e.debug),createQuadTree:r(p),createBounds:i(p),createDragForce:a(p),createSpringForce:o(p),integrate:s(p)},c[p]=f}var m=f.Body,h=f.createQuadTree,g=f.createBounds,_=f.createDragForce,v=f.createSpringForce,y=f.integrate,ee=e=>new m(e),b=Ze().random(42),x=[],S=[],C=h(e,b),w=g(x,e,b),T=v(e,b),E=_(e),D=0,O=[],k=new Map,A=0;N(`nbody`,ne),N(`spring`,re);var j={bodies:x,quadTree:C,springs:S,settings:e,addForce:N,removeForce:P,getForces:te,step:function(){for(var t=0;t<O.length;++t)O[t](A);var n=y(x,e.timeStep,e.adaptiveTimeStepWeight);return A+=1,n},addBody:function(e){if(!e)throw Error(`Body is required`);return x.push(e),e},addBodyAt:function(e){if(!e)throw Error(`Body position is required`);var t=ee(e);return x.push(t),t},removeBody:function(e){if(e){var t=x.indexOf(e);if(!(t<0))return x.splice(t,1),x.length===0&&w.reset(),!0}},addSpring:function(e,n,r,i){if(!e||!n)throw Error(`Cannot add null spring to force simulator`);typeof r!=`number`&&(r=-1);var a=new t(e,n,r,i>=0?i:-1);return S.push(a),a},getTotalMovement:function(){return D},removeSpring:function(e){if(e){var t=S.indexOf(e);if(t>-1)return S.splice(t,1),!0}},getBestNewBodyPosition:function(e){return w.getBestNewPosition(e)},getBBox:M,getBoundingBox:M,invalidateBBox:function(){console.warn("invalidateBBox() is deprecated, bounds always recomputed on `getBBox()` call")},gravity:function(t){return t===void 0?e.gravity:(e.gravity=t,C.options({gravity:t}),this)},theta:function(t){return t===void 0?e.theta:(e.theta=t,C.options({theta:t}),this)},random:b};return u(e,j),d(j),j;function M(){return w.update(),w.box}function N(e,t){if(k.has(e))throw Error(`Force `+e+` is already added`);k.set(e,t),O.push(t)}function P(e){var t=O.indexOf(k.get(e));t<0||(O.splice(t,1),k.delete(e))}function te(){return k}function ne(){if(x.length!==0){C.insertBodies(x);for(var e=x.length;e--;){var t=x[e];t.isPinned||(t.reset(),C.updateBodyForce(t),E.update(t))}}}function re(){for(var e=S.length;e--;)T.update(S[e])}}function u(e,t){for(var n in e)d(e,t,n)}function d(e,t,n){e.hasOwnProperty(n)&&typeof t[n]!=`function`&&(t[n]=Number.isFinite(e[n])?function(r){if(r!==void 0){if(!Number.isFinite(r))throw Error(`Value of `+n+` should be a valid number.`);return e[n]=r,t}return e[n]}:function(r){return r===void 0?e[n]:(e[n]=r,t)})}})),$e=r(t(((e,t)=>{t.exports=r,t.exports.simulator=Qe();var n=Xe();function r(e,t){if(!e)throw Error(`Graph structure cannot be undefined`);var r=(t&&t.createSimulator||Qe())(t);if(Array.isArray(t))throw Error(`Physics settings is expected to be an object`);var a=e.version>19?k:O;t&&typeof t.nodeMass==`function`&&(a=t.nodeMass);var o=new Map,s={},c=0,l=r.settings.springTransform||i;ee(),_();var u=!1,d={step:function(){if(c===0)return f(!0),!0;var e=r.step();d.lastMove=e,d.fire(`step`);var t=e/c<=.01;return f(t),t},getNodePosition:function(e){return D(e).pos},setNodePosition:function(e){var t=D(e);t.setPosition.apply(t,Array.prototype.slice.call(arguments,1))},getLinkPosition:function(e){var t=s[e];if(t)return{from:t.from.pos,to:t.to.pos}},getGraphRect:function(){return r.getBBox()},forEachBody:p,pinNode:function(e,t){var n=D(e.id);n.isPinned=!!t},isNodePinned:function(e){return D(e.id).isPinned},dispose:function(){e.off(`changed`,y),d.fire(`disposed`)},getBody:g,getSpring:h,getForceVectorLength:m,simulator:r,graph:e,lastMove:0};return n(d),d;function f(e){u!==e&&(u=e,v(e))}function p(e){o.forEach(e)}function m(){var e=0,t=0;return p(function(n){e+=Math.abs(n.force.x),t+=Math.abs(n.force.y)}),Math.sqrt(e*e+t*t)}function h(t,n){var r;if(n===void 0)r=typeof t==`object`?t.id:t;else{var i=e.hasLink(t,n);if(!i)return;r=i.id}return s[r]}function g(e){return o.get(e)}function _(){e.on(`changed`,y)}function v(e){d.fire(`stable`,e)}function y(t){for(var n=0;n<t.length;++n){var r=t[n];r.changeType===`add`?(r.node&&b(r.node.id),r.link&&S(r.link)):r.changeType===`remove`&&(r.node&&x(r.node),r.link&&C(r.link))}c=e.getNodesCount()}function ee(){c=0,e.forEachNode(function(e){b(e.id),c+=1}),e.forEachLink(S)}function b(t){var n=o.get(t);if(!n){var i=e.getNode(t);if(!i)throw Error(`initBody() was called with unknown node id`);var a=i.position;if(!a){var s=w(i);a=r.getBestNewBodyPosition(s)}n=r.addBodyAt(a),n.id=t,o.set(t,n),T(t),E(i)&&(n.isPinned=!0)}}function x(e){var t=e.id,n=o.get(t);n&&(o.delete(t),r.removeBody(n))}function S(e){T(e.fromId),T(e.toId);var t=o.get(e.fromId),n=o.get(e.toId),i=r.addSpring(t,n,e.length);l(e,i),s[e.id]=i}function C(t){var n=s[t.id];if(n){var i=e.getNode(t.fromId),a=e.getNode(t.toId);i&&T(i.id),a&&T(a.id),delete s[t.id],r.removeSpring(n)}}function w(e){var t=[];if(!e.links)return t;for(var n=Math.min(e.links.length,2),r=0;r<n;++r){var i=e.links[r],a=i.fromId===e.id?o.get(i.toId):o.get(i.fromId);a&&a.pos&&t.push(a)}return t}function T(e){var t=o.get(e);if(t.mass=a(e),Number.isNaN(t.mass))throw Error(`Node mass should be a number`)}function E(e){return e&&(e.isPinned||e.data&&e.data.isPinned)}function D(e){var t=o.get(e);return t||=(b(e),o.get(e)),t}function O(t){var n=e.getLinks(t);return n?1+n.length/3:1}function k(t){var n=e.getLinks(t);return n?1+n.size/3:1}}function i(){}}))(),1);function et(e,t){(t==null||t>e.length)&&(t=e.length);for(var n=0,r=Array(t);n<t;n++)r[n]=e[n];return r}function tt(e){if(Array.isArray(e))return e}function nt(e){if(Array.isArray(e))return et(e)}function rt(e,t,n){if(typeof e==`function`?e===t:e.has(t))return arguments.length<3?t:n;throw TypeError(`Private element is not present on this object`)}function it(e,t){if(t.has(e))throw TypeError(`Cannot initialize the same private elements twice on an object`)}function at(e,t){if(!(e instanceof t))throw TypeError(`Cannot call a class as a function`)}function R(e,t){return e.get(rt(e,t))}function z(e,t,n){it(e,t),t.set(e,n)}function ot(e,t,n){return e.set(rt(e,t),n),n}function st(e,t){for(var n=0;n<t.length;n++){var r=t[n];r.enumerable=r.enumerable||!1,r.configurable=!0,`value`in r&&(r.writable=!0),Object.defineProperty(e,gt(r.key),r)}}function ct(e,t,n){return t&&st(e.prototype,t),Object.defineProperty(e,"prototype",{writable:!1}),e}function lt(e){if(typeof Symbol<`u`&&e[Symbol.iterator]!=null||e[`@@iterator`]!=null)return Array.from(e)}function ut(e,t){var n=e==null?null:typeof Symbol<`u`&&e[Symbol.iterator]||e[`@@iterator`];if(n!=null){var r,i,a,o,s=[],c=!0,l=!1;try{if(a=(n=n.call(e)).next,t!==0)for(;!(c=(r=a.call(n)).done)&&(s.push(r.value),s.length!==t);c=!0);}catch(e){l=!0,i=e}finally{try{if(!c&&n.return!=null&&(o=n.return(),Object(o)!==o))return}finally{if(l)throw i}}return s}}function dt(){throw TypeError(`Invalid attempt to destructure non-iterable instance.
In order to be iterable, non-array objects must have a [Symbol.iterator]() method.`)}function ft(){throw TypeError(`Invalid attempt to spread non-iterable instance.
In order to be iterable, non-array objects must have a [Symbol.iterator]() method.`)}function pt(e,t){return tt(e)||ut(e,t)||_t(e,t)||dt()}function mt(e){return nt(e)||lt(e)||_t(e)||ft()}function ht(e,t){if(typeof e!=`object`||!e)return e;var n=e[Symbol.toPrimitive];if(n!==void 0){var r=n.call(e,t);if(typeof r!=`object`)return r;throw TypeError(`@@toPrimitive must return a primitive value.`)}return String(e)}function gt(e){var t=ht(e,`string`);return typeof t==`symbol`?t:t+``}function _t(e,t){if(e){if(typeof e==`string`)return et(e,t);var n={}.toString.call(e).slice(8,-1);return n===`Object`&&e.constructor&&(n=e.constructor.name),n===`Map`||n===`Set`?Array.from(e):n===`Arguments`||/^(?:Ui|I)nt(?:8|16|32)(?:Clamped)?Array$/.test(n)?et(e,t):void 0}}var B=new WeakMap,vt=new WeakMap,V=new WeakMap,yt=new WeakMap,bt=new WeakMap,xt=new WeakMap,St=function(){function e(){at(this,e),z(this,B,new Map),z(this,vt,new Map),z(this,V,function(e){return e}),z(this,yt,function(){return{}}),z(this,bt,function(){}),z(this,xt,function(){})}return ct(e,[{key:`getObj`,value:function(e){return R(B,this).get(R(V,this).call(this,e))}},{key:`getData`,value:function(e){return R(vt,this).get(e)}},{key:`entries`,value:function(){return mt(R(vt,this).entries()).map(function(e){var t=pt(e,2),n=t[0];return[t[1],n]})}},{key:`id`,value:function(e){return ot(V,this,h(e)),this}},{key:`onCreateObj`,value:function(e){return ot(yt,this,e),this}},{key:`onUpdateObj`,value:function(e){return ot(bt,this,e),this}},{key:`onRemoveObj`,value:function(e){return ot(xt,this,e),this}},{key:`digest`,value:function(e){var t=this;e.filter(function(e){return!R(B,t).has(R(V,t).call(t,e))}).forEach(function(e){var n=R(yt,t).call(t,e);R(B,t).set(R(V,t).call(t,e),n),R(vt,t).set(n,e)});var n=new Map(e.map(function(e){return[R(V,t).call(t,e),e]}));return R(B,this).forEach(function(e,r){n.has(r)?R(bt,t).call(t,e,n.get(r)):(R(xt,t).call(t,e,r),R(B,t).delete(r),R(vt,t).delete(e))}),this}},{key:`clear`,value:function(){return this.digest([]),this}}])}(),Ct=e=>{e instanceof Array?e.forEach(Ct):(e.map&&e.map.dispose(),e.dispose())},wt=e=>{e.geometry&&e.geometry.dispose(),e.material&&Ct(e.material),e.texture&&e.texture.dispose(),e.children&&e.children.forEach(wt)},Tt=e=>{for(;e.children.length;){let t=e.children[0];e.remove(t),wt(t)}},Et=class extends St{constructor(e,{dataBindAttr:t=`__data`,objBindAttr:n=`__threeObj`}={}){super(),this.scene=e,this.#e=t,this.#t=n,this.onRemoveObj(()=>{})}onCreateObj(e){return super.onCreateObj(t=>{let n=e(t);return t[this.#t]=n,n[this.#e]=t,this.scene.add(n),n}),this}onRemoveObj(e){return super.onRemoveObj((t,n)=>{let r=super.getData(t);e(t,n),this.scene.remove(t),wt(t),delete r[this.#t]}),this}scene;#e;#t},Dt=e=>isNaN(e)?parseInt(v(e).toHex(),16):e,Ot=e=>isNaN(e)?v(e).getAlpha():1,kt=_(s);function At(e,t,n){t&&typeof n==`string`&&e.filter(e=>!e[n]).forEach(e=>{e[n]=kt(t(e))})}function jt({nodes:e,links:t},n,{nodeFilter:r=()=>!0,onLoopError:i=e=>{throw`Invalid DAG structure! Found cycle in node path: ${e.join(` -> `)}.`}}={}){let a={};e.forEach(e=>a[n(e)]={data:e,out:[],depth:-1,skip:!r(e)}),t.forEach(({source:e,target:t})=>{let r=c(e),i=c(t);if(!a.hasOwnProperty(r))throw`Missing source node with id: ${r}`;if(!a.hasOwnProperty(i))throw`Missing target node with id: ${i}`;let o=a[r],s=a[i];o.out.push(s);function c(e){return typeof e==`object`?n(e):e}});let o=[];return s(Object.values(a)),Object.assign({},...Object.entries(a).filter(([,e])=>!e.skip).map(([e,t])=>({[e]:t.depth})));function s(e,t=[],r=0){for(let a=0,c=e.length;a<c;a++){let c=e[a];if(t.indexOf(c)!==-1){let e=[...t.slice(t.indexOf(c)),c].map(e=>n(e.data));o.some(t=>t.length===e.length&&t.every((t,n)=>t===e[n]))||(o.push(e),i(e))}else r>c.depth&&(c.depth=r,s(c.out,[...t,c],r+ +!c.skip))}}}var H=window.THREE?window.THREE:{Group:De,Mesh:O,MeshLambertMaterial:P,Color:be,BufferGeometry:de,BufferAttribute:ue,Matrix4:ie,Vector3:ae,SphereGeometry:k,CylinderGeometry:fe,TubeGeometry:ke,ConeGeometry:_e,Line:re,LineBasicMaterial:E,QuadraticBezierCurve3:me,CubicBezierCurve3:pe,Box3:we},Mt={graph:Fe,forcelayout:$e.default},Nt=2,Pt=new H.BufferGeometry().setAttribute?`setAttribute`:`addAttribute`,Ft=new H.BufferGeometry().applyMatrix4?`applyMatrix4`:`applyMatrix`,It=f({props:{jsonUrl:{onChange:function(e,t){e&&!t.fetchingJson&&(t.fetchingJson=!0,t.onLoading(),fetch(e).then(e=>e.json()).then(e=>{t.fetchingJson=!1,t.onFinishLoading(e),this.graphData(e)}))},triggerUpdate:!1},graphData:{default:{nodes:[],links:[]},onChange(e,t){t.engineRunning=!1}},numDimensions:{default:3,onChange(e,t){let n=t.d3ForceLayout.force(`charge`);n&&n.strength(e>2?-60:-30),e<3&&r(t.graphData.nodes,`z`),e<2&&r(t.graphData.nodes,`y`);function r(e,t){e.forEach(e=>{delete e[t],delete e[`v${t}`]})}}},dagMode:{onChange(e,t){!e&&t.forceEngine===`d3`&&(t.graphData.nodes||[]).forEach(e=>e.fx=e.fy=e.fz=void 0)}},dagLevelDistance:{},dagNodeFilter:{default:e=>!0},onDagError:{triggerUpdate:!1},nodeRelSize:{default:4},nodeId:{default:`id`},nodeVal:{default:`val`},nodeResolution:{default:8},nodeColor:{default:`color`},nodeAutoColorBy:{},nodeOpacity:{default:.75},nodeVisibility:{default:!0},nodeThreeObject:{},nodeThreeObjectExtend:{default:!1},nodePositionUpdate:{triggerUpdate:!1},linkSource:{default:`source`},linkTarget:{default:`target`},linkVisibility:{default:!0},linkColor:{default:`color`},linkAutoColorBy:{},linkOpacity:{default:.2},linkWidth:{},linkResolution:{default:6},linkCurvature:{default:0,triggerUpdate:!1},linkCurveRotation:{default:0,triggerUpdate:!1},linkMaterial:{},linkThreeObject:{},linkThreeObjectExtend:{default:!1},linkPositionUpdate:{triggerUpdate:!1},linkDirectionalArrowLength:{default:0},linkDirectionalArrowColor:{},linkDirectionalArrowRelPos:{default:.5,triggerUpdate:!1},linkDirectionalArrowResolution:{default:8},linkDirectionalParticles:{default:0},linkDirectionalParticleSpeed:{default:.01,triggerUpdate:!1},linkDirectionalParticleOffset:{default:0,triggerUpdate:!1},linkDirectionalParticleWidth:{default:.5},linkDirectionalParticleColor:{},linkDirectionalParticleResolution:{default:4},linkDirectionalParticleThreeObject:{},forceEngine:{default:`d3`},d3AlphaMin:{default:0},d3AlphaDecay:{default:.0228,triggerUpdate:!1,onChange(e,t){t.d3ForceLayout.alphaDecay(e)}},d3AlphaTarget:{default:0,triggerUpdate:!1,onChange(e,t){t.d3ForceLayout.alphaTarget(e)}},d3VelocityDecay:{default:.4,triggerUpdate:!1,onChange(e,t){t.d3ForceLayout.velocityDecay(e)}},ngraphPhysics:{default:{timeStep:20,gravity:-1.2,theta:.8,springLength:30,springCoefficient:8e-4,dragCoefficient:.02}},warmupTicks:{default:0,triggerUpdate:!1},cooldownTicks:{default:1/0,triggerUpdate:!1},cooldownTime:{default:15e3,triggerUpdate:!1},onLoading:{default:()=>{},triggerUpdate:!1},onFinishLoading:{default:()=>{},triggerUpdate:!1},onUpdate:{default:()=>{},triggerUpdate:!1},onFinishUpdate:{default:()=>{},triggerUpdate:!1},onEngineTick:{default:()=>{},triggerUpdate:!1},onEngineStop:{default:()=>{},triggerUpdate:!1}},methods:{refresh:function(e){return e._flushObjects=!0,e._rerender(),this},d3Force:function(e,t,n){return n===void 0?e.d3ForceLayout.force(t):(e.d3ForceLayout.force(t,n),this)},d3ReheatSimulation:function(e){return e.d3ForceLayout.alpha(1),this.resetCountdown(),this},resetCountdown:function(e){return e.cntTicks=0,e.startTickTime=new Date,e.engineRunning=!0,this},tickFrame:function(e){let t=e.forceEngine!==`ngraph`;return e.engineRunning&&e.layout&&n(),r(),i(),this;function n(){++e.cntTicks>e.cooldownTicks||new Date-e.startTickTime>e.cooldownTime||t&&e.d3AlphaMin>0&&e.d3ForceLayout.alpha()<e.d3AlphaMin?(e.engineRunning=!1,e.onEngineStop()):(e.layout[t?`tick`:`step`](),e.onEngineTick());let n=h(e.nodeThreeObjectExtend);e.nodeDataMapper.entries().forEach(([r,i])=>{if(!i)return;let a=t?r:e.layout.getNodePosition(r[e.nodeId]),o=n(r);e.nodePositionUpdate&&e.nodePositionUpdate(o?i.children[0]:i,{x:a.x,y:a.y,z:a.z},r)&&!o||(i.position.x=a.x,i.position.y=a.y||0,i.position.z=a.z||0)});let r=h(e.linkWidth),i=h(e.linkCurvature),a=h(e.linkCurveRotation),o=h(e.linkThreeObjectExtend);e.linkDataMapper.entries().forEach(([n,i])=>{if(!i)return;let a=t?n:e.layout.getLinkPosition(e.layout.graph.getLink(n.source,n.target).id),c=a[t?`source`:`from`],l=a[t?`target`:`to`];if(!c||!l||!c.hasOwnProperty(`x`)||!l.hasOwnProperty(`x`))return;s(n);let u=o(n);if(e.linkPositionUpdate&&e.linkPositionUpdate(u?i.children[1]:i,{start:{x:c.x,y:c.y,z:c.z},end:{x:l.x,y:l.y,z:l.z}},n)&&!u)return;let d=n.__curve,f=i.children.length?i.children[0]:i;if(f.type===`Line`){if(d){let e=d.getPoints(30);f.geometry.getAttribute(`position`).array.length!==e.length*3&&f.geometry[Pt](`position`,new H.BufferAttribute(new Float32Array(e.length*3),3)),f.geometry.setFromPoints(e)}else{let e=f.geometry.getAttribute(`position`);e&&e.array&&e.array.length===6||f.geometry[Pt](`position`,e=new H.BufferAttribute(new Float32Array(6),3)),e.array[0]=c.x,e.array[1]=c.y||0,e.array[2]=c.z||0,e.array[3]=l.x,e.array[4]=l.y||0,e.array[5]=l.z||0,e.needsUpdate=!0}f.geometry.computeBoundingSphere()}else if(f.type===`Mesh`){if(d){f.geometry.type.match(/^Tube(Buffer)?Geometry$/)||(f.position.set(0,0,0),f.rotation.set(0,0,0),f.scale.set(1,1,1));let t=Math.ceil(r(n)*10)/10/2,i=new H.TubeGeometry(d,30,t,e.linkResolution,!1);f.geometry.dispose(),f.geometry=i}else{if(!f.geometry.type.match(/^Cylinder(Buffer)?Geometry$/)){let t=Math.ceil(r(n)*10)/10/2,i=new H.CylinderGeometry(t,t,1,e.linkResolution,1,!1);i[Ft](new H.Matrix4().makeTranslation(0,1/2,0)),i[Ft](new H.Matrix4().makeRotationX(Math.PI/2)),f.geometry.dispose(),f.geometry=i}let t=new H.Vector3(c.x,c.y||0,c.z||0),i=new H.Vector3(l.x,l.y||0,l.z||0),a=t.distanceTo(i);f.position.x=t.x,f.position.y=t.y,f.position.z=t.z,f.scale.z=a,f.parent.localToWorld(i),f.lookAt(i)}}});function s(n){let r=t?n:e.layout.getLinkPosition(e.layout.graph.getLink(n.source,n.target).id),o=r[t?`source`:`from`],s=r[t?`target`:`to`];if(!o||!s||!o.hasOwnProperty(`x`)||!s.hasOwnProperty(`x`))return;let c=i(n);if(!c)n.__curve=null;else{let e=new H.Vector3(o.x,o.y||0,o.z||0),t=new H.Vector3(s.x,s.y||0,s.z||0),r=e.distanceTo(t),i,l=a(n);if(r>0){let n=s.x-o.x,r=s.y-o.y||0,a=new H.Vector3().subVectors(t,e),u=a.clone().multiplyScalar(c).cross(n!==0||r!==0?new H.Vector3(0,0,1):new H.Vector3(0,1,0)).applyAxisAngle(a.normalize(),l).add(new H.Vector3().addVectors(e,t).divideScalar(2));i=new H.QuadraticBezierCurve3(e,u,t)}else{let n=c*70,r=-l,a=r+Math.PI/2;i=new H.CubicBezierCurve3(e,new H.Vector3(n*Math.cos(a),n*Math.sin(a),0).add(e),new H.Vector3(n*Math.cos(r),n*Math.sin(r),0).add(e),t)}n.__curve=i}}}function r(){let n=h(e.linkDirectionalArrowRelPos),r=h(e.linkDirectionalArrowLength),i=h(e.nodeVal);e.arrowDataMapper.entries().forEach(([a,o])=>{if(!o)return;let s=t?a:e.layout.getLinkPosition(e.layout.graph.getLink(a.source,a.target).id),c=s[t?`source`:`from`],l=s[t?`target`:`to`];if(!c||!l||!c.hasOwnProperty(`x`)||!l.hasOwnProperty(`x`))return;let u=Math.cbrt(Math.max(0,i(c)||1))*e.nodeRelSize,d=Math.cbrt(Math.max(0,i(l)||1))*e.nodeRelSize,f=r(a),p=n(a),m=a.__curve?e=>a.__curve.getPoint(e):e=>{let t=(e,t,n,r)=>t[e]+(n[e]-t[e])*r||0;return{x:t(`x`,c,l,e),y:t(`y`,c,l,e),z:t(`z`,c,l,e)}},h=a.__curve?a.__curve.getLength():Math.sqrt([`x`,`y`,`z`].map(e=>((l[e]||0)-(c[e]||0))**2).reduce((e,t)=>e+t,0)),g=u+f+(h-u-d-f)*p,_=m(g/h),v=m((g-f)/h);[`x`,`y`,`z`].forEach(e=>o.position[e]=v[e]);let y=new H.Vector3(...[`x`,`y`,`z`].map(e=>_[e]));o.parent.localToWorld(y),o.lookAt(y)})}function i(){let n=h(e.linkDirectionalParticleSpeed),r=h(e.linkDirectionalParticleOffset);e.graphData.links.forEach(i=>{let a=e.particlesDataMapper.getObj(i),o=a&&a.children,s=i.__singleHopPhotonsObj&&i.__singleHopPhotonsObj.children;if((!s||!s.length)&&(!o||!o.length))return;let c=t?i:e.layout.getLinkPosition(e.layout.graph.getLink(i.source,i.target).id),l=c[t?`source`:`from`],u=c[t?`target`:`to`];if(!l||!u||!l.hasOwnProperty(`x`)||!u.hasOwnProperty(`x`))return;let d=n(i),f=Math.abs(r(i)),p=i.__curve?e=>i.__curve.getPoint(e):e=>{let t=(e,t,n,r)=>t[e]+(n[e]-t[e])*r||0;return{x:t(`x`,l,u,e),y:t(`y`,l,u,e),z:t(`z`,l,u,e)}};[...o||[],...s||[]].forEach((e,t)=>{let n=e.parent.__linkThreeObjType===`singleHopPhotons`;if(e.hasOwnProperty(`__progressRatio`)||(e.__progressRatio=n?+(d<0):(t+f)/o.length),e.__progressRatio+=d,e.__progressRatio>=1||e.__progressRatio<0){if(!n)e.__progressRatio%=1,e.__progressRatio<0&&e.__progressRatio++;else{e.parent.remove(e),Tt(e);return}}let r=e.__progressRatio,i=p(r);e.geometry.type!==`SphereGeometry`&&e.lookAt(i.x,i.y,i.z),[`x`,`y`,`z`].forEach(t=>e.position[t]=i[t])})})}},emitParticle:function(e,t){if(t&&e.graphData.links.includes(t)){if(!t.__singleHopPhotonsObj){let n=new H.Group;n.__linkThreeObjType=`singleHopPhotons`,t.__singleHopPhotonsObj=n,e.graphScene.add(n)}let n=h(e.linkDirectionalParticleThreeObject)(t);if(n&&e.linkDirectionalParticleThreeObject===n&&(n=n.clone()),!n){let r=h(e.linkDirectionalParticleWidth),i=Math.ceil(r(t)*10)/10/2,a=e.linkDirectionalParticleResolution,o=new H.SphereGeometry(i,a,a),s=h(e.linkColor),c=h(e.linkDirectionalParticleColor)(t)||s(t)||`#f0f0f0`,l=new H.Color(Dt(c)),u=e.linkOpacity*3,d=new H.MeshLambertMaterial({color:l,transparent:!0,opacity:u});n=new H.Mesh(o,d)}t.__singleHopPhotonsObj.add(n)}return this},getGraphBbox:function(e,t=()=>!0){if(!e.initialised)return null;let n=function e(n){let r=[];if(n.geometry){n.geometry.computeBoundingBox();let e=new H.Box3;e.copy(n.geometry.boundingBox).applyMatrix4(n.matrixWorld),r.push(e)}return r.concat(...(n.children||[]).filter(e=>!e.hasOwnProperty(`__graphObjType`)||e.__graphObjType===`node`&&t(e.__data)).map(e))}(e.graphScene);return n.length?Object.assign(...[`x`,`y`,`z`].map(e=>({[e]:[C(n,t=>t.min[e]),a(n,t=>t.max[e])]}))):null}},stateInit:()=>({d3ForceLayout:m().force(`link`,x()).force(`charge`,c()).force(`center`,l()).force(`dagRadial`,null).stop(),engineRunning:!1}),init(e,t){t.graphScene=e,t.nodeDataMapper=new Et(e,{objBindAttr:`__threeObj`}),t.linkDataMapper=new Et(e,{objBindAttr:`__lineObj`}),t.arrowDataMapper=new Et(e,{objBindAttr:`__arrowObj`}),t.particlesDataMapper=new Et(e,{objBindAttr:`__photonsObj`})},update(e,t){let n=e=>e.some(e=>t.hasOwnProperty(e));if(e.engineRunning=!1,typeof e.onUpdate==`function`&&e.onUpdate(),e.nodeAutoColorBy!==null&&n([`nodeAutoColorBy`,`graphData`,`nodeColor`])&&At(e.graphData.nodes,h(e.nodeAutoColorBy),e.nodeColor),e.linkAutoColorBy!==null&&n([`linkAutoColorBy`,`graphData`,`linkColor`])&&At(e.graphData.links,h(e.linkAutoColorBy),e.linkColor),e._flushObjects||n([`graphData`,`nodeThreeObject`,`nodeThreeObjectExtend`,`nodeVal`,`nodeColor`,`nodeVisibility`,`nodeRelSize`,`nodeResolution`,`nodeOpacity`])){let t=h(e.nodeThreeObject),r=h(e.nodeThreeObjectExtend),i=h(e.nodeVal),a=h(e.nodeColor),o=h(e.nodeVisibility),s={},c={};(e._flushObjects||n([`nodeThreeObject`,`nodeThreeObjectExtend`]))&&e.nodeDataMapper.clear(),e.nodeDataMapper.onCreateObj(n=>{let i=t(n),a=r(n);i&&e.nodeThreeObject===i&&(i=i.clone());let o;return i&&!a?o=i:(o=new H.Mesh,o.__graphDefaultObj=!0,i&&a&&o.add(i)),o.__graphObjType=`node`,o}).onUpdateObj((t,n)=>{if(t.__graphDefaultObj){let r=i(n)||1,o=Math.cbrt(r)*e.nodeRelSize,l=e.nodeResolution;t.geometry.type.match(/^Sphere(Buffer)?Geometry$/)&&t.geometry.parameters.radius===o&&t.geometry.parameters.widthSegments===l||(s.hasOwnProperty(r)||(s[r]=new H.SphereGeometry(o,l,l)),t.geometry.dispose(),t.geometry=s[r]);let u=a(n),d=new H.Color(Dt(u||`#ffffaa`)),f=e.nodeOpacity*Ot(u);t.material.type===`MeshLambertMaterial`&&t.material.color.equals(d)&&t.material.opacity===f||(c.hasOwnProperty(u)||(c[u]=new H.MeshLambertMaterial({color:d,transparent:!0,opacity:f})),t.material.dispose(),t.material=c[u])}}).digest(e.graphData.nodes.filter(o))}if(e._flushObjects||n([`graphData`,`linkThreeObject`,`linkThreeObjectExtend`,`linkMaterial`,`linkColor`,`linkWidth`,`linkVisibility`,`linkResolution`,`linkOpacity`,`linkDirectionalArrowLength`,`linkDirectionalArrowColor`,`linkDirectionalArrowResolution`,`linkDirectionalParticles`,`linkDirectionalParticleWidth`,`linkDirectionalParticleColor`,`linkDirectionalParticleResolution`,`linkDirectionalParticleThreeObject`])){let r=h(e.linkThreeObject),i=h(e.linkThreeObjectExtend),a=h(e.linkMaterial),o=h(e.linkVisibility),s=h(e.linkColor),c=h(e.linkWidth),l={},u={},d={},f=e.graphData.links.filter(o);if((e._flushObjects||n([`linkThreeObject`,`linkThreeObjectExtend`,`linkWidth`]))&&e.linkDataMapper.clear(),e.linkDataMapper.onRemoveObj(e=>{let t=e.__data&&e.__data.__singleHopPhotonsObj;t&&(t.parent.remove(t),Tt(t),delete e.__data.__singleHopPhotonsObj)}).onCreateObj(t=>{let n=r(t),a=i(t);n&&e.linkThreeObject===n&&(n=n.clone());let o;if(!n||a){if(c(t))o=new H.Mesh;else{let e=new H.BufferGeometry;e[Pt](`position`,new H.BufferAttribute(new Float32Array(6),3)),o=new H.Line(e)}}let s;return n?a?(s=new H.Group,s.__graphDefaultObj=!0,s.add(o),s.add(n)):s=n:(s=o,s.__graphDefaultObj=!0),s.renderOrder=10,s.__graphObjType=`link`,s}).onUpdateObj((t,n)=>{if(t.__graphDefaultObj){let r=t.children.length?t.children[0]:t,i=Math.ceil(c(n)*10)/10,o=!!i;if(o){let t=i/2,n=e.linkResolution;if(!r.geometry.type.match(/^Cylinder(Buffer)?Geometry$/)||r.geometry.parameters.radiusTop!==t||r.geometry.parameters.radialSegments!==n){if(!l.hasOwnProperty(i)){let e=new H.CylinderGeometry(t,t,1,n,1,!1);e[Ft](new H.Matrix4().makeTranslation(0,1/2,0)),e[Ft](new H.Matrix4().makeRotationX(Math.PI/2)),l[i]=e}r.geometry.dispose(),r.geometry=l[i]}}let f=a(n);if(f)r.material=f;else{let t=s(n),i=new H.Color(Dt(t||`#f0f0f0`)),a=e.linkOpacity*Ot(t),c=o?`MeshLambertMaterial`:`LineBasicMaterial`;if(r.material.type!==c||!r.material.color.equals(i)||r.material.opacity!==a){let e=o?u:d;e.hasOwnProperty(t)||(e[t]=new H[c]({color:i,transparent:a<1,opacity:a,depthWrite:a>=1})),r.material.dispose(),r.material=e[t]}}}}).digest(f),e.linkDirectionalArrowLength||t.hasOwnProperty(`linkDirectionalArrowLength`)){let t=h(e.linkDirectionalArrowLength),n=h(e.linkDirectionalArrowColor);e.arrowDataMapper.onCreateObj(()=>{let e=new H.Mesh(void 0,new H.MeshLambertMaterial({transparent:!0}));return e.__linkThreeObjType=`arrow`,e}).onUpdateObj((r,i)=>{let a=t(i),o=e.linkDirectionalArrowResolution;if(!r.geometry.type.match(/^Cone(Buffer)?Geometry$/)||r.geometry.parameters.height!==a||r.geometry.parameters.radialSegments!==o){let e=new H.ConeGeometry(a*.25,a,o);e.translate(0,a/2,0),e.rotateX(Math.PI/2),r.geometry.dispose(),r.geometry=e}let c=n(i)||s(i)||`#f0f0f0`;r.material.color=new H.Color(Dt(c)),r.material.opacity=e.linkOpacity*3*Ot(c)}).digest(f.filter(t))}if(e.linkDirectionalParticles||t.hasOwnProperty(`linkDirectionalParticles`)){let t=h(e.linkDirectionalParticles),n=h(e.linkDirectionalParticleWidth),r=h(e.linkDirectionalParticleColor),i=h(e.linkDirectionalParticleThreeObject),a={},o={};e.particlesDataMapper.onCreateObj(()=>{let e=new H.Group;return e.__linkThreeObjType=`photons`,e.__photonDataMapper=new Et(e),e}).onUpdateObj((c,l)=>{let u=!!c.children.length&&c.children[0],d=i(l),f,p;if(d)f=d.geometry,p=d.material;else{let t=Math.ceil(n(l)*10)/10/2,i=e.linkDirectionalParticleResolution;u&&u.geometry.parameters.radius===t&&u.geometry.parameters.widthSegments===i?f=u.geometry:(o.hasOwnProperty(t)||(o[t]=new H.SphereGeometry(t,i,i)),f=o[t]);let c=r(l)||s(l)||`#f0f0f0`,d=new H.Color(Dt(c)),m=e.linkOpacity*3;u&&u.material.color.equals(d)&&u.material.opacity===m?p=u.material:(a.hasOwnProperty(c)||(a[c]=new H.MeshLambertMaterial({color:d,transparent:!0,opacity:m})),p=a[c])}u&&(u.geometry!==f&&u.geometry.dispose(),u.material!==p&&u.material.dispose());let m=Math.round(Math.abs(t(l)));c.__photonDataMapper.id(e=>e.idx).onCreateObj(()=>new H.Mesh(f,p)).onUpdateObj(e=>{e.geometry=f,e.material=p}).digest([...Array(m)].map((e,t)=>({idx:t})))}).digest(f.filter(t))}}if(e._flushObjects=!1,n([`graphData`,`nodeId`,`linkSource`,`linkTarget`,`numDimensions`,`forceEngine`,`dagMode`,`dagNodeFilter`,`dagLevelDistance`])){e.engineRunning=!1,e.graphData.links.forEach(t=>{t.source=t[e.linkSource],t.target=t[e.linkTarget]});let n=e.forceEngine!==`ngraph`,r;if(n){(r=e.d3ForceLayout).stop().alpha(1).numDimensions(e.numDimensions).nodes(e.graphData.nodes);let n=e.d3ForceLayout.force(`link`);n&&n.id(t=>t[e.nodeId]).links(e.graphData.links);let i=e.dagMode&&jt(e.graphData,t=>t[e.nodeId],{nodeFilter:e.dagNodeFilter,onLoopError:e.onDagError||void 0}),a=Math.max(...Object.values(i||[])),o=e.dagLevelDistance||e.graphData.nodes.length/(a||1)*Nt*([`radialin`,`radialout`].indexOf(e.dagMode)===-1?1:.7);if([`lr`,`rl`,`td`,`bu`,`zin`,`zout`].includes(t.dagMode)){let n=[`lr`,`rl`].includes(t.dagMode)?`fx`:[`td`,`bu`].includes(t.dagMode)?`fy`:`fz`;e.graphData.nodes.filter(e.dagNodeFilter).forEach(e=>delete e[n])}if([`lr`,`rl`,`td`,`bu`,`zin`,`zout`].includes(e.dagMode)){let t=[`rl`,`td`,`zout`].includes(e.dagMode),n=n=>(i[n[e.nodeId]]-a/2)*o*(t?-1:1),r=[`lr`,`rl`].includes(e.dagMode)?`fx`:[`td`,`bu`].includes(e.dagMode)?`fy`:`fz`;e.graphData.nodes.filter(e.dagNodeFilter).forEach(e=>e[r]=n(e))}e.d3ForceLayout.force(`dagRadial`,[`radialin`,`radialout`].indexOf(e.dagMode)===-1?null:ee(t=>{let n=i[t[e.nodeId]]||-1;return(e.dagMode===`radialin`?a-n:n)*o}).strength(t=>+!!e.dagNodeFilter(t)))}else{let t=Mt.graph();e.graphData.nodes.forEach(n=>{t.addNode(n[e.nodeId])}),e.graphData.links.forEach(e=>{t.addLink(e.source,e.target)}),r=Mt.forcelayout(t,{dimensions:e.numDimensions,...e.ngraphPhysics}),r.graph=t}for(let t=0;t<e.warmupTicks&&!(n&&e.d3AlphaMin>0&&e.d3ForceLayout.alpha()<e.d3AlphaMin);t++)r[n?`tick`:`step`]();e.layout=r,this.resetCountdown()}e.engineRunning=!0,e.onFinishUpdate()}});function Lt(e,t=Object,n=!1){class r extends t{constructor(...t){super(...t),this.__kapsuleInstance=new e(...n?[this]:[],...t)}}let i=e();return i._destructor?.(),Object.keys(i).forEach(e=>r.prototype[e]=function(...t){let n=this.__kapsuleInstance[e](...t);return n===this.__kapsuleInstance?this:n}),r}var Rt=Lt(It,(window.THREE?window.THREE:{Group:De}).Group,!0),zt=class{constructor(){throw Error(`WebGPU rendering is not bundled in AutoOntology`)}};function U(){return U=Object.assign?Object.assign.bind():function(e){for(var t=1;t<arguments.length;t++){var n=arguments[t];for(var r in n)({}).hasOwnProperty.call(n,r)&&(e[r]=n[r])}return e},U.apply(null,arguments)}function Bt(e){if(e===void 0)throw ReferenceError(`this hasn't been initialised - super() hasn't been called`);return e}function Vt(e,t){return Vt=Object.setPrototypeOf?Object.setPrototypeOf.bind():function(e,t){return e.__proto__=t,e},Vt(e,t)}function Ht(e,t){e.prototype=Object.create(t.prototype),e.prototype.constructor=e,Vt(e,t)}function Ut(e){return Ut=Object.setPrototypeOf?Object.getPrototypeOf.bind():function(e){return e.__proto__||Object.getPrototypeOf(e)},Ut(e)}function Wt(e){try{return Function.toString.call(e).indexOf(`[native code]`)!==-1}catch{return typeof e==`function`}}function Gt(){try{var e=!Boolean.prototype.valueOf.call(Reflect.construct(Boolean,[],function(){}))}catch{}return(Gt=function(){return!!e})()}function Kt(e,t,n){if(Gt())return Reflect.construct.apply(null,arguments);var r=[null];r.push.apply(r,t);var i=new(e.bind.apply(e,r));return n&&Vt(i,n.prototype),i}function qt(e){var t=typeof Map==`function`?new Map:void 0;return qt=function(e){if(e===null||!Wt(e))return e;if(typeof e!=`function`)throw TypeError(`Super expression must either be null or a function`);if(t!==void 0){if(t.has(e))return t.get(e);t.set(e,n)}function n(){return Kt(e,arguments,Ut(this).constructor)}return n.prototype=Object.create(e.prototype,{constructor:{value:n,enumerable:!1,writable:!0,configurable:!0}}),Vt(n,e)},qt(e)}var W=function(e){Ht(t,e);function t(t){return Bt(e.call(this,`An error occurred. See https://github.com/styled-components/polished/blob/main/src/internalHelpers/errors.md#`+t+` for more information.`)||this)}return t}(qt(Error));function Jt(e){return Math.round(e*255)}function Yt(e,t,n){return Jt(e)+`,`+Jt(t)+`,`+Jt(n)}function Xt(e,t,n,r){if(r===void 0&&(r=Yt),t===0)return r(n,n,n);var i=(e%360+360)%360/60,a=(1-Math.abs(2*n-1))*t,o=a*(1-Math.abs(i%2-1)),s=0,c=0,l=0;i>=0&&i<1?(s=a,c=o):i>=1&&i<2?(s=o,c=a):i>=2&&i<3?(c=a,l=o):i>=3&&i<4?(c=o,l=a):i>=4&&i<5?(s=o,l=a):i>=5&&i<6&&(s=a,l=o);var u=n-a/2,d=s+u,f=c+u,p=l+u;return r(d,f,p)}var Zt={aliceblue:`f0f8ff`,antiquewhite:`faebd7`,aqua:`00ffff`,aquamarine:`7fffd4`,azure:`f0ffff`,beige:`f5f5dc`,bisque:`ffe4c4`,black:`000`,blanchedalmond:`ffebcd`,blue:`0000ff`,blueviolet:`8a2be2`,brown:`a52a2a`,burlywood:`deb887`,cadetblue:`5f9ea0`,chartreuse:`7fff00`,chocolate:`d2691e`,coral:`ff7f50`,cornflowerblue:`6495ed`,cornsilk:`fff8dc`,crimson:`dc143c`,cyan:`00ffff`,darkblue:`00008b`,darkcyan:`008b8b`,darkgoldenrod:`b8860b`,darkgray:`a9a9a9`,darkgreen:`006400`,darkgrey:`a9a9a9`,darkkhaki:`bdb76b`,darkmagenta:`8b008b`,darkolivegreen:`556b2f`,darkorange:`ff8c00`,darkorchid:`9932cc`,darkred:`8b0000`,darksalmon:`e9967a`,darkseagreen:`8fbc8f`,darkslateblue:`483d8b`,darkslategray:`2f4f4f`,darkslategrey:`2f4f4f`,darkturquoise:`00ced1`,darkviolet:`9400d3`,deeppink:`ff1493`,deepskyblue:`00bfff`,dimgray:`696969`,dimgrey:`696969`,dodgerblue:`1e90ff`,firebrick:`b22222`,floralwhite:`fffaf0`,forestgreen:`228b22`,fuchsia:`ff00ff`,gainsboro:`dcdcdc`,ghostwhite:`f8f8ff`,gold:`ffd700`,goldenrod:`daa520`,gray:`808080`,green:`008000`,greenyellow:`adff2f`,grey:`808080`,honeydew:`f0fff0`,hotpink:`ff69b4`,indianred:`cd5c5c`,indigo:`4b0082`,ivory:`fffff0`,khaki:`f0e68c`,lavender:`e6e6fa`,lavenderblush:`fff0f5`,lawngreen:`7cfc00`,lemonchiffon:`fffacd`,lightblue:`add8e6`,lightcoral:`f08080`,lightcyan:`e0ffff`,lightgoldenrodyellow:`fafad2`,lightgray:`d3d3d3`,lightgreen:`90ee90`,lightgrey:`d3d3d3`,lightpink:`ffb6c1`,lightsalmon:`ffa07a`,lightseagreen:`20b2aa`,lightskyblue:`87cefa`,lightslategray:`789`,lightslategrey:`789`,lightsteelblue:`b0c4de`,lightyellow:`ffffe0`,lime:`0f0`,limegreen:`32cd32`,linen:`faf0e6`,magenta:`f0f`,maroon:`800000`,mediumaquamarine:`66cdaa`,mediumblue:`0000cd`,mediumorchid:`ba55d3`,mediumpurple:`9370db`,mediumseagreen:`3cb371`,mediumslateblue:`7b68ee`,mediumspringgreen:`00fa9a`,mediumturquoise:`48d1cc`,mediumvioletred:`c71585`,midnightblue:`191970`,mintcream:`f5fffa`,mistyrose:`ffe4e1`,moccasin:`ffe4b5`,navajowhite:`ffdead`,navy:`000080`,oldlace:`fdf5e6`,olive:`808000`,olivedrab:`6b8e23`,orange:`ffa500`,orangered:`ff4500`,orchid:`da70d6`,palegoldenrod:`eee8aa`,palegreen:`98fb98`,paleturquoise:`afeeee`,palevioletred:`db7093`,papayawhip:`ffefd5`,peachpuff:`ffdab9`,peru:`cd853f`,pink:`ffc0cb`,plum:`dda0dd`,powderblue:`b0e0e6`,purple:`800080`,rebeccapurple:`639`,red:`f00`,rosybrown:`bc8f8f`,royalblue:`4169e1`,saddlebrown:`8b4513`,salmon:`fa8072`,sandybrown:`f4a460`,seagreen:`2e8b57`,seashell:`fff5ee`,sienna:`a0522d`,silver:`c0c0c0`,skyblue:`87ceeb`,slateblue:`6a5acd`,slategray:`708090`,slategrey:`708090`,snow:`fffafa`,springgreen:`00ff7f`,steelblue:`4682b4`,tan:`d2b48c`,teal:`008080`,thistle:`d8bfd8`,tomato:`ff6347`,turquoise:`40e0d0`,violet:`ee82ee`,wheat:`f5deb3`,white:`fff`,whitesmoke:`f5f5f5`,yellow:`ff0`,yellowgreen:`9acd32`};function Qt(e){if(typeof e!=`string`)return e;var t=e.toLowerCase();return Zt[t]?`#`+Zt[t]:e}var $t=/^#[a-fA-F0-9]{6}$/,en=/^#[a-fA-F0-9]{8}$/,tn=/^#[a-fA-F0-9]{3}$/,nn=/^#[a-fA-F0-9]{4}$/,rn=/^rgb\(\s*(\d{1,3})\s*(?:,)?\s*(\d{1,3})\s*(?:,)?\s*(\d{1,3})\s*\)$/i,an=/^rgb(?:a)?\(\s*(\d{1,3})\s*(?:,)?\s*(\d{1,3})\s*(?:,)?\s*(\d{1,3})\s*(?:,|\/)\s*([-+]?\d*[.]?\d+[%]?)\s*\)$/i,on=/^hsl\(\s*(\d{0,3}[.]?[0-9]+(?:deg)?)\s*(?:,)?\s*(\d{1,3}[.]?[0-9]?)%\s*(?:,)?\s*(\d{1,3}[.]?[0-9]?)%\s*\)$/i,sn=/^hsl(?:a)?\(\s*(\d{0,3}[.]?[0-9]+(?:deg)?)\s*(?:,)?\s*(\d{1,3}[.]?[0-9]?)%\s*(?:,)?\s*(\d{1,3}[.]?[0-9]?)%\s*(?:,|\/)\s*([-+]?\d*[.]?\d+[%]?)\s*\)$/i;function G(e){if(typeof e!=`string`)throw new W(3);var t=Qt(e);if(t.match($t))return{red:parseInt(``+t[1]+t[2],16),green:parseInt(``+t[3]+t[4],16),blue:parseInt(``+t[5]+t[6],16)};if(t.match(en)){var n=parseFloat((parseInt(``+t[7]+t[8],16)/255).toFixed(2));return{red:parseInt(``+t[1]+t[2],16),green:parseInt(``+t[3]+t[4],16),blue:parseInt(``+t[5]+t[6],16),alpha:n}}if(t.match(tn))return{red:parseInt(``+t[1]+t[1],16),green:parseInt(``+t[2]+t[2],16),blue:parseInt(``+t[3]+t[3],16)};if(t.match(nn)){var r=parseFloat((parseInt(``+t[4]+t[4],16)/255).toFixed(2));return{red:parseInt(``+t[1]+t[1],16),green:parseInt(``+t[2]+t[2],16),blue:parseInt(``+t[3]+t[3],16),alpha:r}}var i=rn.exec(t);if(i)return{red:parseInt(``+i[1],10),green:parseInt(``+i[2],10),blue:parseInt(``+i[3],10)};var a=an.exec(t.substring(0,50));if(a)return{red:parseInt(``+a[1],10),green:parseInt(``+a[2],10),blue:parseInt(``+a[3],10),alpha:parseFloat(``+a[4])>1?parseFloat(``+a[4])/100:parseFloat(``+a[4])};var o=on.exec(t);if(o){var s=`rgb(`+Xt(parseInt(``+o[1],10),parseInt(``+o[2],10)/100,parseInt(``+o[3],10)/100)+`)`,c=rn.exec(s);if(!c)throw new W(4,t,s);return{red:parseInt(``+c[1],10),green:parseInt(``+c[2],10),blue:parseInt(``+c[3],10)}}var l=sn.exec(t.substring(0,50));if(l){var u=`rgb(`+Xt(parseInt(``+l[1],10),parseInt(``+l[2],10)/100,parseInt(``+l[3],10)/100)+`)`,d=rn.exec(u);if(!d)throw new W(4,t,u);return{red:parseInt(``+d[1],10),green:parseInt(``+d[2],10),blue:parseInt(``+d[3],10),alpha:parseFloat(``+l[4])>1?parseFloat(``+l[4])/100:parseFloat(``+l[4])}}throw new W(5)}function cn(e){var t=e.red/255,n=e.green/255,r=e.blue/255,i=Math.max(t,n,r),a=Math.min(t,n,r),o=(i+a)/2;if(i===a)return e.alpha===void 0?{hue:0,saturation:0,lightness:o}:{hue:0,saturation:0,lightness:o,alpha:e.alpha};var s,c=i-a,l=o>.5?c/(2-i-a):c/(i+a);switch(i){case t:s=(n-r)/c+(n<r?6:0);break;case n:s=(r-t)/c+2;break;default:s=(t-n)/c+4}return s*=60,e.alpha===void 0?{hue:s,saturation:l,lightness:o}:{hue:s,saturation:l,lightness:o,alpha:e.alpha}}function K(e){return cn(G(e))}var ln=function(e){return e.length===7&&e[1]===e[2]&&e[3]===e[4]&&e[5]===e[6]?`#`+e[1]+e[3]+e[5]:e};function q(e){var t=e.toString(16);return t.length===1?`0`+t:t}function un(e){return q(Math.round(e*255))}function dn(e,t,n){return ln(`#`+un(e)+un(t)+un(n))}function fn(e,t,n){return Xt(e,t,n,dn)}function pn(e,t,n){if(typeof e==`number`&&typeof t==`number`&&typeof n==`number`)return fn(e,t,n);if(typeof e==`object`&&t===void 0&&n===void 0)return fn(e.hue,e.saturation,e.lightness);throw new W(1)}function mn(e,t,n,r){if(typeof e==`number`&&typeof t==`number`&&typeof n==`number`&&typeof r==`number`)return r>=1?fn(e,t,n):`rgba(`+Xt(e,t,n)+`,`+r+`)`;if(typeof e==`object`&&t===void 0&&n===void 0&&r===void 0)return e.alpha>=1?fn(e.hue,e.saturation,e.lightness):`rgba(`+Xt(e.hue,e.saturation,e.lightness)+`,`+e.alpha+`)`;throw new W(2)}function hn(e,t,n){if(typeof e==`number`&&typeof t==`number`&&typeof n==`number`)return ln(`#`+q(e)+q(t)+q(n));if(typeof e==`object`&&t===void 0&&n===void 0)return ln(`#`+q(e.red)+q(e.green)+q(e.blue));throw new W(6)}function gn(e,t,n,r){if(typeof e==`string`&&typeof t==`number`){var i=G(e);return`rgba(`+i.red+`,`+i.green+`,`+i.blue+`,`+t+`)`}if(typeof e==`number`&&typeof t==`number`&&typeof n==`number`&&typeof r==`number`)return r>=1?hn(e,t,n):`rgba(`+e+`,`+t+`,`+n+`,`+r+`)`;if(typeof e==`object`&&t===void 0&&n===void 0&&r===void 0)return e.alpha>=1?hn(e.red,e.green,e.blue):`rgba(`+e.red+`,`+e.green+`,`+e.blue+`,`+e.alpha+`)`;throw new W(7)}var _n=function(e){return typeof e.red==`number`&&typeof e.green==`number`&&typeof e.blue==`number`&&(typeof e.alpha!=`number`||e.alpha===void 0)},vn=function(e){return typeof e.red==`number`&&typeof e.green==`number`&&typeof e.blue==`number`&&typeof e.alpha==`number`},yn=function(e){return typeof e.hue==`number`&&typeof e.saturation==`number`&&typeof e.lightness==`number`&&(typeof e.alpha!=`number`||e.alpha===void 0)},bn=function(e){return typeof e.hue==`number`&&typeof e.saturation==`number`&&typeof e.lightness==`number`&&typeof e.alpha==`number`};function J(e){if(typeof e!=`object`)throw new W(8);if(vn(e))return gn(e);if(_n(e))return hn(e);if(bn(e))return mn(e);if(yn(e))return pn(e);throw new W(8)}function xn(e,t,n){return function(){var r=n.concat(Array.prototype.slice.call(arguments));return r.length>=t?e.apply(this,r):xn(e,t,r)}}function Y(e){return xn(e,e.length,[])}function Sn(e,t){if(t===`transparent`)return t;var n=K(t);return J(U({},n,{hue:n.hue+parseFloat(e)}))}Y(Sn);function X(e,t,n){return Math.max(e,Math.min(t,n))}function Cn(e,t){if(t===`transparent`)return t;var n=K(t);return J(U({},n,{lightness:X(0,1,n.lightness-parseFloat(e))}))}Y(Cn);function wn(e,t){if(t===`transparent`)return t;var n=K(t);return J(U({},n,{saturation:X(0,1,n.saturation-parseFloat(e))}))}Y(wn);function Tn(e,t){if(t===`transparent`)return t;var n=K(t);return J(U({},n,{lightness:X(0,1,n.lightness+parseFloat(e))}))}Y(Tn);function En(e,t,n){if(t===`transparent`)return n;if(n===`transparent`)return t;if(e===0)return n;var r=G(t),i=U({},r,{alpha:typeof r.alpha==`number`?r.alpha:1}),a=G(n),o=U({},a,{alpha:typeof a.alpha==`number`?a.alpha:1}),s=i.alpha-o.alpha,c=parseFloat(e)*2-1,l=((c*s===-1?c:c+s)/(1+c*s)+1)/2,u=1-l;return gn({red:Math.floor(i.red*l+o.red*u),green:Math.floor(i.green*l+o.green*u),blue:Math.floor(i.blue*l+o.blue*u),alpha:i.alpha*parseFloat(e)+o.alpha*(1-parseFloat(e))})}var Dn=Y(En);function On(e,t){if(t===`transparent`)return t;var n=G(t),r=typeof n.alpha==`number`?n.alpha:1;return gn(U({},n,{alpha:X(0,1,(r*100+parseFloat(e)*100)/100)}))}var kn=Y(On);function An(e,t){if(t===`transparent`)return t;var n=K(t);return J(U({},n,{saturation:X(0,1,n.saturation+parseFloat(e))}))}Y(An);function jn(e,t){return t===`transparent`?t:J(U({},K(t),{hue:parseFloat(e)}))}Y(jn);function Mn(e,t){return t===`transparent`?t:J(U({},K(t),{lightness:parseFloat(e)}))}Y(Mn);function Nn(e,t){return t===`transparent`?t:J(U({},K(t),{saturation:parseFloat(e)}))}Y(Nn);function Pn(e,t){return t===`transparent`?t:Dn(parseFloat(e),`rgb(0, 0, 0)`,t)}Y(Pn);function Fn(e,t){return t===`transparent`?t:Dn(parseFloat(e),`rgb(255, 255, 255)`,t)}Y(Fn);function In(e,t){if(t===`transparent`)return t;var n=G(t),r=typeof n.alpha==`number`?n.alpha:1;return gn(U({},n,{alpha:X(0,1,(r*100-parseFloat(e)*100).toFixed(2)/100)}))}Y(In);function Ln(e,t){t===void 0&&(t={});var n=t.insertAt;if(typeof document<`u`){var r=document.head||document.getElementsByTagName(`head`)[0],i=document.createElement(`style`);i.type=`text/css`,n===`top`&&r.firstChild?r.insertBefore(i,r.firstChild):r.appendChild(i),i.styleSheet?i.styleSheet.cssText=e:i.appendChild(document.createTextNode(e))}}Ln(`.scene-nav-info {
  position: absolute;
  bottom: 5px;
  width: 100%;
  text-align: center;
  color: slategrey;
  opacity: 0.7;
  font-size: 10px;
  font-family: sans-serif;
  pointer-events: none;
  user-select: none;
}

.scene-container canvas:focus {
  outline: none;
}`);var Rn=e=>{e instanceof Array?e.forEach(Rn):(e.map&&e.map.dispose(),e.dispose())},zn=e=>{e.geometry&&e.geometry.dispose(),e.material&&Rn(e.material),e.texture&&e.texture.dispose(),e.children&&e.children.forEach(zn)},Bn=e=>{for(;e.children.length;){let t=e.children[0];e.remove(t),zn(t)}},Z=window.THREE?window.THREE:{WebGLRenderer:le,Scene:te,PerspectiveCamera:w,Raycaster:M,SRGBColorSpace:N,TextureLoader:A,Vector2:T,Vector3:ae,Box3:we,Color:be,Mesh:O,SphereGeometry:k,MeshBasicMaterial:D,BackSide:1,Timer:j},Vn=f({props:{width:{default:window.innerWidth,onChange(e,t,n){isNaN(e)&&(t.width=n)}},height:{default:window.innerHeight,onChange(e,t,n){isNaN(e)&&(t.height=n)}},viewOffset:{default:[0,0]},backgroundColor:{default:`#000011`},backgroundImageUrl:{},onBackgroundImageLoaded:{},showNavInfo:{default:!0},skyRadius:{default:5e4},objects:{default:[]},lights:{default:[]},enablePointerInteraction:{default:!0,onChange(e,t){t.hoverObj=null,t.tooltip&&t.tooltip.content(null)},triggerUpdate:!1},pointerRaycasterThrottleMs:{default:50,triggerUpdate:!1},lineHoverPrecision:{default:1,triggerUpdate:!1},pointsHoverPrecision:{default:1,triggerUpdate:!1},hoverOrderComparator:{triggerUpdate:!1},hoverFilter:{default:()=>!0,triggerUpdate:!1},tooltipContent:{triggerUpdate:!1},hoverDuringDrag:{default:!1,triggerUpdate:!1},clickAfterDrag:{default:!1,triggerUpdate:!1},onHover:{default:()=>{},triggerUpdate:!1},onClick:{default:()=>{},triggerUpdate:!1},onRightClick:{triggerUpdate:!1}},methods:{tick:function(e){if(e.initialised){e.controls.enabled&&e.controls.update&&e.controls.update(Math.min(1,e.timer.update().getDelta())),e.postProcessingComposer?e.postProcessingComposer.render():e.renderer.render(e.scene,e.camera),e.extraRenderers.forEach(t=>t.render(e.scene,e.camera));let t=+new Date;if(e.enablePointerInteraction&&t-e.lastRaycasterCheck>=e.pointerRaycasterThrottleMs){e.lastRaycasterCheck=t;let n=null;if(e.hoverDuringDrag||!e.isPointerDragging){let t=this.intersectingObjects(e.pointerPos.x,e.pointerPos.y);e.hoverOrderComparator&&t.sort((t,n)=>e.hoverOrderComparator(t.object,n.object));let r=t.find(t=>e.hoverFilter(t.object))||null;n=r?r.object:null,e.intersection=r||null}n!==e.hoverObj&&(e.onHover(n,e.hoverObj,e.intersection),e.tooltip.content(n&&h(e.tooltipContent)(n,e.intersection)||null),e.hoverObj=n)}e.tweenGroup.update()}return this},getPointerPos:function(e){let{x:t,y:n}=e.pointerPos;return{x:t,y:n}},cameraPosition:function(e,t,n,r){let i=e.camera;if(t&&e.initialised){let c=t,l=n||{x:0,y:0,z:0};if(e.povPosTween?.end(),e.povTgtTween?.end(),!r)a(c),o(l);else{let t=Object.assign({},i.position),n=s();e.tweenGroup.add(e.povPosTween=new S(t).to(c,r).easing(d.Quadratic.Out).onUpdate(a).onComplete(function(){e.povPosTween=void 0,e.tweenGroup.remove(this)}).start()),e.tweenGroup.add(e.povTgtTween=new S(n).to(l,r/3).easing(d.Quadratic.Out).onUpdate(o).onComplete(function(){e.povTgtTween=void 0,e.tweenGroup.remove(this)}).start())}return this}return Object.assign({},i.position,{lookAt:s()});function a(e){let{x:t,y:n,z:r}=e;t!==void 0&&(i.position.x=t),n!==void 0&&(i.position.y=n),r!==void 0&&(i.position.z=r)}function o(t){let n=new Z.Vector3(t.x,t.y,t.z);e.controls.enabled&&e.controls.target?e.controls.target=n:i.lookAt(n)}function s(){return Object.assign(new Z.Vector3(0,0,-1e3).applyQuaternion(i.quaternion).add(i.position))}},zoomToFit:function(e,t=0,n=10,r,i){return this.fitToBbox(this.getBbox(r),t,n,i)},fitToBbox:function(e,t,n=0,r=10,i=!1){let a=e.camera;if(t){a.updateMatrixWorld();let o=new Z.Vector3,s=new Z.Vector3,c=new Z.Vector3,l=new Z.Vector3;a.getWorldPosition(l),a.getWorldDirection(c),o.crossVectors(c,a.up).normalize(),s.crossVectors(o,c).normalize();let u=(1-r*2/e.height)*a.fov/2*(Math.PI/180),d=Math.atan(Math.tan(u)*a.aspect),f=new Z.Vector3((t.x[0]+t.x[1])/2,(t.y[0]+t.y[1])/2,(t.z[0]+t.z[1])/2),p=i?c.clone().multiplyScalar(l.distanceTo(f)).add(l):f,m=Math.max(0,...[new Z.Vector3(t.x[0],t.y[0],t.z[0]),new Z.Vector3(t.x[0],t.y[0],t.z[1]),new Z.Vector3(t.x[0],t.y[1],t.z[0]),new Z.Vector3(t.x[0],t.y[1],t.z[1]),new Z.Vector3(t.x[1],t.y[0],t.z[0]),new Z.Vector3(t.x[1],t.y[0],t.z[1]),new Z.Vector3(t.x[1],t.y[1],t.z[0]),new Z.Vector3(t.x[1],t.y[1],t.z[1])].map(e=>{let t=e.clone().sub(p);return Math.max(Math.abs(t.dot(s))/Math.tan(u),Math.abs(t.dot(o))/Math.tan(d))-t.dot(c)})),h=c.clone().negate(),g={x:p.x+h.x*m,y:p.y+h.y*m,z:p.z+h.z*m};this.cameraPosition(g,p,n)}return this},getBbox:function(e,t=()=>!0){let n=new Z.Box3,r=e.objects.filter(t);return r.length?(r.forEach(e=>n.expandByObject(e)),Object.assign(...[`x`,`y`,`z`].map(e=>({[e]:[n.min[e],n.max[e]]})))):null},getScreenCoords:function(e,t,n,r){let i=new Z.Vector3(t,n,r);return i.project(this.camera()),{x:(i.x+1)*e.width/2,y:-(i.y-1)*e.height/2}},getSceneCoords:function(e,t,n,r=0){let i=new Z.Vector2(t/e.width*2-1,-(n/e.height)*2+1),a=new Z.Raycaster;return a.setFromCamera(i,e.camera),Object.assign({},a.ray.at(r,new Z.Vector3))},intersectingObjects:function(e,t,n){let r=new Z.Vector2(t/e.width*2-1,-(n/e.height)*2+1),i=new Z.Raycaster;return i.params.Line.threshold=e.lineHoverPrecision,i.params.Points.threshold=e.pointsHoverPrecision,i.setFromCamera(r,e.camera),i.intersectObjects(e.objects,!0)},renderer:e=>e.renderer,scene:e=>e.scene,camera:e=>e.camera,postProcessingComposer:e=>e.postProcessingComposer,controls:e=>e.controls,tbControls:e=>e.controls,_destructor:function(e){Bn(e.scene),e.controls?.dispose(),e.renderer?.dispose(),e.postProcessingComposer?.dispose()}},stateInit:()=>({scene:new Z.Scene,camera:new Z.PerspectiveCamera,timer:new Z.Timer,tweenGroup:new o,lastRaycasterCheck:0}),init(e,t,{controlType:n=`trackball`,useWebGPU:r=!1,rendererConfig:i={},extraRenderers:a=[],waitForLoadComplete:o=!0}={}){e.innerHTML=``,e.appendChild(t.container=document.createElement(`div`)),t.container.className=`scene-container`,t.container.style.position=`relative`,t.container.appendChild(t.navInfo=document.createElement(`div`)),t.navInfo.className=`scene-nav-info`,t.navInfo.textContent={orbit:`Left-click: rotate, Mouse-wheel/middle-click: zoom, Right-click: pan`,trackball:`Left-click: rotate, Mouse-wheel/middle-click: zoom, Right-click: pan`,fly:`WASD: move, R|F: up | down, Q|E: roll, up|down: pitch, left|right: yaw`}[n]||``,t.navInfo.style.display=t.showNavInfo?null:`none`,t.tooltip=new u(t.container),t.pointerPos=new Z.Vector2,t.pointerPos.x=-2,t.pointerPos.y=-2,[`pointermove`,`pointerdown`].forEach(e=>t.container.addEventListener(e,n=>{if(e===`pointerdown`&&(t.isPointerPressed=!0),!t.isPointerDragging&&n.type===`pointermove`&&(n.pressure>0||t.isPointerPressed)&&(n.pointerType===`mouse`||n.movementX===void 0||[n.movementX,n.movementY].some(e=>Math.abs(e)>1))&&(t.isPointerDragging=!0),t.enablePointerInteraction){let e=r(t.container);t.pointerPos.x=n.pageX-e.left,t.pointerPos.y=n.pageY-e.top}function r(e){let t=e.getBoundingClientRect(),n=window.pageXOffset||document.documentElement.scrollLeft,r=window.pageYOffset||document.documentElement.scrollTop;return{top:t.top+r,left:t.left+n}}},{passive:!0})),t.container.addEventListener(`pointerup`,e=>{t.isPointerPressed&&(t.isPointerPressed=!1,!(t.isPointerDragging&&(t.isPointerDragging=!1,!t.clickAfterDrag))&&requestAnimationFrame(()=>{e.button===0&&t.onClick(t.hoverObj||null,e,t.intersection),e.button===2&&t.onRightClick&&t.onRightClick(t.hoverObj||null,e,t.intersection)}))},{passive:!0,capture:!0}),t.container.addEventListener(`contextmenu`,e=>{t.onRightClick&&e.preventDefault()}),t.renderer=new(r?zt:Z.WebGLRenderer)(Object.assign({antialias:!0,alpha:!0},i)),t.renderer.setPixelRatio(Math.min(2,window.devicePixelRatio)),t.container.appendChild(t.renderer.domElement),t.extraRenderers=a,t.extraRenderers.forEach(e=>{e.domElement.style.position=`absolute`,e.domElement.style.top=`0px`,e.domElement.style.pointerEvents=`none`,t.container.appendChild(e.domElement)}),t.postProcessingComposer=new xe(t.renderer),t.postProcessingComposer.addPass(new ve(t.scene,t.camera)),t.controls=new{trackball:ye,orbit:se,fly:F}[n](t.camera,t.renderer.domElement),n===`fly`&&(t.controls.movementSpeed=300,t.controls.rollSpeed=Math.PI/6,t.controls.dragToLook=!0),(n===`trackball`||n===`orbit`)&&(t.controls.minDistance=.1,t.controls.maxDistance=t.skyRadius,t.controls.addEventListener(`start`,()=>{t.controlsEngaged=!0}),t.controls.addEventListener(`change`,()=>{t.controlsEngaged&&(t.controlsDragging=!0)}),t.controls.addEventListener(`end`,()=>{t.controlsEngaged=!1,t.controlsDragging=!1})),[t.renderer,t.postProcessingComposer,...t.extraRenderers].forEach(e=>e.setSize(t.width,t.height)),t.camera.aspect=t.width/t.height,t.camera.updateProjectionMatrix(),t.camera.position.z=1e3,t.scene.add(t.skysphere=new Z.Mesh),t.skysphere.visible=!1,t.loadComplete=t.scene.visible=!o,window.scene=t.scene},update(e,t){if(e.width&&e.height&&(t.hasOwnProperty(`width`)||t.hasOwnProperty(`height`))){let t=e.width,n=e.height;e.container.style.width=`${t}px`,e.container.style.height=`${n}px`,[e.renderer,e.postProcessingComposer,...e.extraRenderers].forEach(e=>e.setSize(t,n)),e.camera.aspect=t/n;let r=e.viewOffset.slice(0,2);r.some(e=>e)&&e.camera.setViewOffset(t,n,...r,t,n),e.camera.updateProjectionMatrix()}if(t.hasOwnProperty(`viewOffset`)){let t=e.width,n=e.height,r=e.viewOffset.slice(0,2);r.some(e=>e)?e.camera.setViewOffset(t,n,...r,t,n):e.camera.clearViewOffset()}if(t.hasOwnProperty(`skyRadius`)&&e.skyRadius&&(e.controls.hasOwnProperty(`maxDistance`)&&t.skyRadius&&(e.controls.maxDistance=Math.min(e.controls.maxDistance,e.skyRadius)),e.camera.far=e.skyRadius*2.5,e.camera.updateProjectionMatrix(),e.skysphere.geometry=new Z.SphereGeometry(e.skyRadius)),t.hasOwnProperty(`backgroundColor`)){let t=G(e.backgroundColor).alpha;t===void 0&&(t=1),e.renderer.setClearColor(new Z.Color(kn(1,e.backgroundColor)),t)}t.hasOwnProperty(`backgroundImageUrl`)&&(e.backgroundImageUrl?new Z.TextureLoader().load(e.backgroundImageUrl,t=>{t.colorSpace=Z.SRGBColorSpace,e.skysphere.material=new Z.MeshBasicMaterial({map:t,side:Z.BackSide}),e.skysphere.visible=!0,e.onBackgroundImageLoaded&&setTimeout(e.onBackgroundImageLoaded),!e.loadComplete&&n()}):(e.skysphere.visible=!1,e.skysphere.material.map=null,!e.loadComplete&&n())),t.hasOwnProperty(`showNavInfo`)&&(e.navInfo.style.display=e.showNavInfo?null:`none`),t.hasOwnProperty(`lights`)&&((t.lights||[]).forEach(t=>e.scene.remove(t)),e.lights.forEach(t=>e.scene.add(t))),t.hasOwnProperty(`objects`)&&((t.objects||[]).forEach(t=>e.scene.remove(t)),e.objects.forEach(t=>e.scene.add(t)));function n(){e.loadComplete=e.scene.visible=!0}}});function Hn(e,t){t===void 0&&(t={});var n=t.insertAt;if(typeof document<`u`){var r=document.head||document.getElementsByTagName(`head`)[0],i=document.createElement(`style`);i.type=`text/css`,n===`top`&&r.firstChild?r.insertBefore(i,r.firstChild):r.appendChild(i),i.styleSheet?i.styleSheet.cssText=e:i.appendChild(document.createTextNode(e))}}Hn(`.graph-info-msg {
  top: 50%;
  width: 100%;
  text-align: center;
  color: lavender;
  opacity: 0.7;
  font-size: 22px;
  position: absolute;
  font-family: Sans-serif;
}

.scene-container .clickable {
  cursor: pointer;
}

.scene-container .grabbable {
  cursor: move;
  cursor: grab;
  cursor: -moz-grab;
  cursor: -webkit-grab;
}

.scene-container .grabbable:active {
  cursor: grabbing;
  cursor: -moz-grabbing;
  cursor: -webkit-grabbing;
}`);function Un(e,t){let n=new t;return n._destructor&&n._destructor(),{linkProp:function(t){return{default:n[t](),onChange(n,r){r[e][t](n)},triggerUpdate:!1}},linkMethod:function(t){return function(n,...r){let i=n[e],a=i[t](...r);return a===i?this:a}}}}var Wn=window.THREE?window.THREE:{AmbientLight:ge,DirectionalLight:oe,REVISION:`186`},Gn=170,Kn=Un(`forceGraph`,Rt),qn=Object.assign(...`jsonUrl.graphData.numDimensions.dagMode.dagLevelDistance.dagNodeFilter.onDagError.nodeRelSize.nodeId.nodeVal.nodeResolution.nodeColor.nodeAutoColorBy.nodeOpacity.nodeVisibility.nodeThreeObject.nodeThreeObjectExtend.nodePositionUpdate.linkSource.linkTarget.linkVisibility.linkColor.linkAutoColorBy.linkOpacity.linkWidth.linkResolution.linkCurvature.linkCurveRotation.linkMaterial.linkThreeObject.linkThreeObjectExtend.linkPositionUpdate.linkDirectionalArrowLength.linkDirectionalArrowColor.linkDirectionalArrowRelPos.linkDirectionalArrowResolution.linkDirectionalParticles.linkDirectionalParticleSpeed.linkDirectionalParticleOffset.linkDirectionalParticleWidth.linkDirectionalParticleColor.linkDirectionalParticleResolution.linkDirectionalParticleThreeObject.forceEngine.d3AlphaDecay.d3VelocityDecay.d3AlphaMin.ngraphPhysics.warmupTicks.cooldownTicks.cooldownTime.onEngineTick.onEngineStop`.split(`.`).map(e=>({[e]:Kn.linkProp(e)}))),Jn=Object.assign(...[`refresh`,`getGraphBbox`,`d3Force`,`d3ReheatSimulation`,`emitParticle`].map(e=>({[e]:Kn.linkMethod(e)}))),Yn=Un(`renderObjs`,Vn),Xn=Object.assign(...[`width`,`height`,`backgroundColor`,`showNavInfo`,`enablePointerInteraction`].map(e=>({[e]:Yn.linkProp(e)}))),Zn=Object.assign(...[`lights`,`cameraPosition`,`postProcessingComposer`].map(e=>({[e]:Yn.linkMethod(e)})),{graph2ScreenCoords:Yn.linkMethod(`getScreenCoords`),screen2GraphCoords:Yn.linkMethod(`getSceneCoords`)}),Qn=f({props:{nodeLabel:{default:`name`,triggerUpdate:!1},linkLabel:{default:`name`,triggerUpdate:!1},linkHoverPrecision:{default:1,onChange:(e,t)=>t.renderObjs.lineHoverPrecision(e),triggerUpdate:!1},enableNavigationControls:{default:!0,onChange(e,t){let n=t.renderObjs.controls();n&&(n.enabled=e,e&&n.domElement&&n.domElement.dispatchEvent(new PointerEvent(`pointerup`)))},triggerUpdate:!1},enableNodeDrag:{default:!0,triggerUpdate:!1},onNodeDrag:{default:()=>{},triggerUpdate:!1},onNodeDragEnd:{default:()=>{},triggerUpdate:!1},onNodeClick:{triggerUpdate:!1},onNodeRightClick:{triggerUpdate:!1},onNodeHover:{triggerUpdate:!1},onLinkClick:{triggerUpdate:!1},onLinkRightClick:{triggerUpdate:!1},onLinkHover:{triggerUpdate:!1},onBackgroundClick:{triggerUpdate:!1},onBackgroundRightClick:{triggerUpdate:!1},showPointerCursor:{default:!0,triggerUpdate:!1},...qn,...Xn},methods:{zoomToFit:function(e,t,n,...r){return e.renderObjs.fitToBbox(e.forceGraph.getGraphBbox(...r),t,n),this},pauseAnimation:function(e){return e.animationFrameRequestId!==null&&(cancelAnimationFrame(e.animationFrameRequestId),e.animationFrameRequestId=null),this},resumeAnimation:function(e){return e.animationFrameRequestId===null&&this._animationCycle(),this},_animationCycle(e){e.enablePointerInteraction&&(this.renderer().domElement.style.cursor=null),e.forceGraph.tickFrame(),e.renderObjs.tick(),e.animationFrameRequestId=requestAnimationFrame(this._animationCycle)},scene:e=>e.renderObjs.scene(),camera:e=>e.renderObjs.camera(),renderer:e=>e.renderObjs.renderer(),controls:e=>e.renderObjs.controls(),_destructor:function(e){this.pauseAnimation(),this.graphData({nodes:[],links:[]}),e.forceGraph._destructor?.(),e.renderObjs._destructor()},...Jn,...Zn},stateInit:({controlType:e,rendererConfig:t,extraRenderers:n})=>{let r=new Rt;return{forceGraph:r,renderObjs:Vn({controlType:e,rendererConfig:t,extraRenderers:n}).objects([r]).lights([new Wn.AmbientLight(13421772,Math.PI),new Wn.DirectionalLight(16777215,.6*Math.PI)])}},init:function(e,t){e.innerHTML=``,e.appendChild(t.container=document.createElement(`div`)),t.container.style.position=`relative`;let n=document.createElement(`div`);t.container.appendChild(n),t.renderObjs(n);let r=t.renderObjs.camera(),i=t.renderObjs.renderer(),a=t.renderObjs.controls();a.enabled=!!t.enableNavigationControls,t.lastSetCameraZ=r.position.z;let o;t.container.appendChild(o=document.createElement(`div`)),o.className=`graph-info-msg`,o.textContent=``,t.forceGraph.onLoading(()=>{o.textContent=`Loading...`}).onFinishLoading(()=>{o.textContent=``}).onUpdate(()=>{t.graphData=t.forceGraph.graphData(),r.position.x===0&&r.position.y===0&&r.position.z===t.lastSetCameraZ&&t.graphData.nodes.length&&(r.lookAt(t.forceGraph.position),t.lastSetCameraZ=r.position.z=Math.cbrt(t.graphData.nodes.length)*Gn)}).onFinishUpdate(()=>{if(t._dragControls){let e=t.graphData.nodes.find(e=>e.__initialFixedPos&&!e.__disposeControlsAfterDrag);e?e.__disposeControlsAfterDrag=!0:t._dragControls.dispose(),t._dragControls=void 0}if(t.enableNodeDrag&&t.enablePointerInteraction&&t.forceEngine===`d3`){let e=t._dragControls=new Se(t.graphData.nodes.map(e=>e.__threeObj).filter(e=>e),r,i.domElement);e.addEventListener(`dragstart`,function(e){let t=Q(e.object);if(!t)return;a.enabled=!1,e.object.__initialPos=e.object.position.clone(),e.object.__prevPos=e.object.position.clone();let n=t.__data;!n.__initialFixedPos&&(n.__initialFixedPos={fx:n.fx,fy:n.fy,fz:n.fz}),!n.__initialPos&&(n.__initialPos={x:n.x,y:n.y,z:n.z}),[`x`,`y`,`z`].forEach(e=>n[`f${e}`]=n[e]),i.domElement.classList.add(`grabbable`)}),e.addEventListener(`drag`,function(e){let n=Q(e.object);if(!n)return;if(!e.object.hasOwnProperty(`__graphObjType`)){let t=e.object.__initialPos,r=e.object.__prevPos,i=e.object.position;n.position.add(i.clone().sub(r)),r.copy(i),i.copy(t)}let r=n.__data,i=n.position,a={x:i.x-r.x,y:i.y-r.y,z:i.z-r.z};[`x`,`y`,`z`].forEach(e=>r[`f${e}`]=r[e]=i[e]),t.forceGraph.d3AlphaTarget(.3).resetCountdown(),r.__dragged=!0,t.onNodeDrag(r,a)}),e.addEventListener(`dragend`,function(n){let r=Q(n.object);if(!r)return;delete n.object.__initialPos,delete n.object.__prevPos;let o=r.__data;o.__disposeControlsAfterDrag&&(e.dispose(),delete o.__disposeControlsAfterDrag);let s=o.__initialFixedPos,c=o.__initialPos,l={x:c.x-o.x,y:c.y-o.y,z:c.z-o.z};s&&([`x`,`y`,`z`].forEach(e=>{let t=`f${e}`;s[t]===void 0&&delete o[t]}),delete o.__initialFixedPos,delete o.__initialPos,o.__dragged&&(delete o.__dragged,t.onNodeDragEnd(o,l))),t.forceGraph.d3AlphaTarget(0).resetCountdown(),t.enableNavigationControls&&(a.enabled=!0,a._status&&a._onPointerCancel?.(),a.domElement&&a.domElement.ownerDocument&&a.domElement.ownerDocument.dispatchEvent(new PointerEvent(`pointerup`,{pointerType:`touch`}))),i.domElement.classList.remove(`grabbable`)})}}),Wn.REVISION<155&&(t.renderObjs.renderer().useLegacyLights=!1),t.renderObjs.hoverOrderComparator((e,t)=>{let n=Q(e);if(!n)return 1;let r=Q(t);if(!r)return-1;let i=e=>e.__graphObjType===`node`;return i(r)-i(n)}).tooltipContent(e=>{let n=Q(e);return n&&h(t[`${n.__graphObjType}Label`])(n.__data)||``}).hoverDuringDrag(!1).onHover(e=>{let n=Q(e);if(n!==t.hoverObj){let e=t.hoverObj?t.hoverObj.__graphObjType:null,r=t.hoverObj?t.hoverObj.__data:null,a=n?n.__graphObjType:null,o=n?n.__data:null;if(e&&e!==a){let n=t[`on${e===`node`?`Node`:`Link`}Hover`];n&&n(null,r)}if(a){let n=t[`on${a===`node`?`Node`:`Link`}Hover`];n&&n(o,e===a?r:null)}i.domElement.classList[(n&&t[`on${a===`node`?`Node`:`Link`}Click`]||!n&&t.onBackgroundClick)&&h(t.showPointerCursor)(o)?`add`:`remove`](`clickable`),t.hoverObj=n}}).clickAfterDrag(!1).onClick((e,n)=>{let r=Q(e);if(r){let e=t[`on${r.__graphObjType===`node`?`Node`:`Link`}Click`];e&&e(r.__data,n)}else t.onBackgroundClick&&t.onBackgroundClick(n)}).onRightClick((e,n)=>{let r=Q(e);if(r){let e=t[`on${r.__graphObjType===`node`?`Node`:`Link`}RightClick`];e&&e(r.__data,n)}else t.onBackgroundRightClick&&t.onBackgroundRightClick(n)}),this._animationCycle()}});function Q(e){let t=e;for(;t&&!t.hasOwnProperty(`__graphObjType`);)t=t.parent;return t}var $=r(p(),1),$n={width:$.default.number,height:$.default.number,graphData:$.default.shape({nodes:$.default.arrayOf($.default.object).isRequired,links:$.default.arrayOf($.default.object).isRequired}),backgroundColor:$.default.string,nodeRelSize:$.default.number,nodeId:$.default.string,nodeLabel:$.default.oneOfType([$.default.string,$.default.func]),nodeVal:$.default.oneOfType([$.default.number,$.default.string,$.default.func]),nodeVisibility:$.default.oneOfType([$.default.bool,$.default.string,$.default.func]),nodeColor:$.default.oneOfType([$.default.string,$.default.func]),nodeAutoColorBy:$.default.oneOfType([$.default.string,$.default.func]),onNodeHover:$.default.func,onNodeClick:$.default.func,linkSource:$.default.string,linkTarget:$.default.string,linkLabel:$.default.oneOfType([$.default.string,$.default.func]),linkVisibility:$.default.oneOfType([$.default.bool,$.default.string,$.default.func]),linkColor:$.default.oneOfType([$.default.string,$.default.func]),linkAutoColorBy:$.default.oneOfType([$.default.string,$.default.func]),linkWidth:$.default.oneOfType([$.default.number,$.default.string,$.default.func]),linkCurvature:$.default.oneOfType([$.default.number,$.default.string,$.default.func]),linkDirectionalArrowLength:$.default.oneOfType([$.default.number,$.default.string,$.default.func]),linkDirectionalArrowColor:$.default.oneOfType([$.default.string,$.default.func]),linkDirectionalArrowRelPos:$.default.oneOfType([$.default.number,$.default.string,$.default.func]),linkDirectionalParticles:$.default.oneOfType([$.default.number,$.default.string,$.default.func]),linkDirectionalParticleSpeed:$.default.oneOfType([$.default.number,$.default.string,$.default.func]),linkDirectionalParticleOffset:$.default.oneOfType([$.default.number,$.default.string,$.default.func]),linkDirectionalParticleWidth:$.default.oneOfType([$.default.number,$.default.string,$.default.func]),linkDirectionalParticleColor:$.default.oneOfType([$.default.string,$.default.func]),onLinkHover:$.default.func,onLinkClick:$.default.func,dagMode:$.default.oneOf([`td`,`bu`,`lr`,`rl`,`zin`,`zout`,`radialin`,`radialout`]),dagLevelDistance:$.default.number,dagNodeFilter:$.default.func,onDagError:$.default.func,d3AlphaMin:$.default.number,d3AlphaDecay:$.default.number,d3VelocityDecay:$.default.number,warmupTicks:$.default.number,cooldownTicks:$.default.number,cooldownTime:$.default.number,onEngineTick:$.default.func,onEngineStop:$.default.func,getGraphBbox:$.default.func},er={zoomToFit:$.default.func,onNodeRightClick:$.default.func,onNodeDrag:$.default.func,onNodeDragEnd:$.default.func,onLinkRightClick:$.default.func,linkHoverPrecision:$.default.number,onBackgroundClick:$.default.func,onBackgroundRightClick:$.default.func,showPointerCursor:$.default.oneOfType([$.default.bool,$.default.func]),enablePointerInteraction:$.default.bool,enableNodeDrag:$.default.bool},tr={showNavInfo:$.default.bool,nodeOpacity:$.default.number,nodeResolution:$.default.number,nodeThreeObject:$.default.oneOfType([$.default.object,$.default.string,$.default.func]),nodeThreeObjectExtend:$.default.oneOfType([$.default.bool,$.default.string,$.default.func]),nodePositionUpdate:$.default.func,linkOpacity:$.default.number,linkResolution:$.default.number,linkCurveRotation:$.default.oneOfType([$.default.number,$.default.string,$.default.func]),linkMaterial:$.default.oneOfType([$.default.object,$.default.string,$.default.func]),linkThreeObject:$.default.oneOfType([$.default.object,$.default.string,$.default.func]),linkThreeObjectExtend:$.default.oneOfType([$.default.bool,$.default.string,$.default.func]),linkPositionUpdate:$.default.func,linkDirectionalArrowResolution:$.default.number,linkDirectionalParticleResolution:$.default.number,linkDirectionalParticleThreeObject:$.default.oneOfType([$.default.object,$.default.string,$.default.func]),forceEngine:$.default.oneOf([`d3`,`ngraph`]),ngraphPhysics:$.default.object,numDimensions:$.default.oneOf([1,2,3])};Object.assign({},$n,er,{linkLineDash:$.default.oneOfType([$.default.arrayOf($.default.number),$.default.string,$.default.func]),nodeCanvasObjectMode:$.default.oneOfType([$.default.string,$.default.func]),nodeCanvasObject:$.default.func,nodePointerAreaPaint:$.default.func,linkCanvasObjectMode:$.default.oneOfType([$.default.string,$.default.func]),linkCanvasObject:$.default.func,linkPointerAreaPaint:$.default.func,linkDirectionalParticleCanvasObject:$.default.func,autoPauseRedraw:$.default.bool,minZoom:$.default.number,maxZoom:$.default.number,enableZoomInteraction:$.default.oneOfType([$.default.bool,$.default.func]),enablePanInteraction:$.default.oneOfType([$.default.bool,$.default.func]),onZoom:$.default.func,onZoomEnd:$.default.func,onRenderFramePre:$.default.func,onRenderFramePost:$.default.func});var nr=Object.assign({},$n,er,tr,{enableNavigationControls:$.default.bool,controlType:$.default.oneOf([`trackball`,`orbit`,`fly`]),rendererConfig:$.default.object,extraRenderers:$.default.arrayOf($.default.shape({render:$.default.func.isRequired}))});Object.assign({},$n,tr,{nodeDesc:$.default.oneOfType([$.default.string,$.default.func]),linkDesc:$.default.oneOfType([$.default.string,$.default.func])}),Object.assign({},$n,tr,{markerAttrs:$.default.object,yOffset:$.default.number,glScale:$.default.number});var rr=i(Qn,{methodNames:[`emitParticle`,`d3Force`,`d3ReheatSimulation`,`stopAnimation`,`pauseAnimation`,`resumeAnimation`,`cameraPosition`,`zoomToFit`,`getGraphBbox`,`screen2GraphCoords`,`graph2ScreenCoords`,`postProcessingComposer`,`lights`,`scene`,`camera`,`renderer`,`controls`,`refresh`],initPropNames:[`controlType`,`rendererConfig`,`extraRenderers`]});rr.displayName=`ForceGraph3D`,rr.propTypes=nr;var ir=25*Math.PI/180,ar=15*Math.PI/180;function or(e,t){if(!(t>0))return 1;let n=e/t;return Math.min(1,Math.max(0,(1.3-n)/.4))}function sr(e,t,n){let r={x:(e.x[0]+e.x[1])/2,y:(e.y[0]+e.y[1])/2,z:(e.z[0]+e.z[1])/2},i=Math.max(20,.5*Math.hypot(e.x[1]-e.x[0],e.y[1]-e.y[0],e.z[1]-e.z[0])),a=t*Math.PI/180,o=2*Math.atan(Math.tan(a/2)*n),s=i/Math.sin(Math.min(a,o)/2)*.9;return{center:r,radius:i,position:{x:r.x+s*Math.cos(ar)*Math.sin(ir),y:r.y+s*Math.sin(ar),z:r.z+s*Math.cos(ar)*Math.cos(ir)}}}var cr=n(),lr=4,ur=.6,dr={light:`#ffffff`,dark:`#0a0a0a`};function fr(e,t,n){for(let r of e){let e=r.__lineObj;if(r.style!==`dashed`||!e?.material)continue;let[i,a]=je(r),o=!t||t.has(i)&&t.has(a),s=e.material;s.color.set(o?r.color??`#8b5cf6`:n),s.opacity=o?.9:.25}}function pr(e,t,n){for(let r of e){let e=t.get(r.id);if(!e)continue;let i=!n.active||n.active.has(r.id);e.mesh.material.color.set(i?r.color:n.dim),e.mesh.material.opacity=i?.95:.35;let a=Math.cbrt(r.val)*lr;e.mesh.scale.setScalar(a),e.ring&&(e.ring.scale.setScalar(a),e.ring.material.opacity=i?.95:.3),e.holder.position.set(0,a+6,0);let o=r.id===n.hoverId||r.id===n.selectedId;e.label.userData.forced=o,e.label.visible=o||(n.active?n.active.has(r.id):r.labelled),e.label.color=n.text}}function mr({nodes:e,links:t,width:n,height:r,dark:i,selectedId:a,active:o,autoRotate:s,showGround:c,onNodeClick:l,onBackgroundClick:u,handleRef:d}){let f=(0,I.useRef)(void 0),p=(0,I.useRef)(new Map),m=(0,I.useRef)(e),h=(0,I.useRef)(0),_=(0,I.useRef)(null),[v,ee]=(0,I.useState)(null),[x,S]=(0,I.useState)(!1),[C,w]=(0,I.useState)(0),T=(0,I.useMemo)(()=>({nodes:e,links:t}),[e,t]),E=i?dr.dark:dr.light,A=i?`#3f3f46`:`#d4d4d8`,j=i?`rgba(212,212,216,0.4)`:`rgba(82,82,91,0.4)`,M=i?`#fafafa`:`#18181b`;(0,I.useEffect)(()=>{m.current=e},[e]);let N=(0,I.useCallback)(e=>{let t=f.current;if(!t)return;let i=t.getGraphBbox();if(!i)return;let a=sr(i,t.camera().fov??50,n/Math.max(1,r));t.cameraPosition(a.position,a.center,e),w(Date.now())},[n,r]),P=(0,I.useRef)(()=>{});(0,I.useImperativeHandle)(d,()=>({focus(e){P.current(),P.current=g(()=>m.current.find(t=>t.id===e),e=>{let t=f.current;if(!t)return;let n={x:e.x??0,y:e.y??0,z:e.z??0},r=140+Math.cbrt(e.val)*lr*4;t.cameraPosition({x:n.x+r*Math.cos(ar)*Math.sin(ir),y:n.y+r*Math.sin(ar),z:n.z+r*Math.cos(ar)*Math.cos(ir)},n,1200)})},reset(){N(800)}}),[N]),(0,I.useEffect)(()=>()=>P.current(),[]),(0,I.useEffect)(()=>{let e=f.current;if(!e)return;let t=new oe(16777215,.9*Math.PI);t.position.set(.7,1.1,.9);let n=new oe(12375295,.45*Math.PI);n.position.set(-.8,-.3,-1),e.lights([new ge(16777215,.4*Math.PI),t,n]);let r=e.scene(),i=new Oe(dr.light,.0025);r.fog=i;let a=e.controls();return a.enableDamping=!0,a.dampingFactor=.08,r.onBeforeRender=(e,t,n)=>{let r=n.position.distanceTo(a.target);h.current=r,r>0&&(i.density=ur/r)},y(e,3),()=>{r.onBeforeRender=()=>{},r.fog=null}},[]),(0,I.useEffect)(()=>{let e=f.current?.scene().fog;e&&(e.color=new be(E))},[E]);let te=(0,I.useRef)(!1),ie=(0,I.useCallback)(()=>{te.current||(te.current=!0,N(900),S(!0),window.setTimeout(()=>S(!1),3900))},[N]);(0,I.useEffect)(()=>{let e=f.current?.controls();e&&(e.autoRotate=s||x,e.autoRotateSpeed=1.2)},[s,x]),(0,I.useEffect)(()=>{let e=f.current;if(!e||!C)return;let t=e.scene();if(_.current&&=(t.remove(_.current),_.current.geometry.dispose(),_.current.material.dispose(),null),!c)return;let n=e.getGraphBbox();if(!n)return;let r=Math.max(n.x[1]-n.x[0],n.z[1]-n.z[0],200)*1.8,a=new ce(r,24,i?5395035:10592682,i?4144966:13948120),o=a.material;o.transparent=!0,o.opacity=i?.35:.5,o.depthWrite=!1,a.position.set((n.x[0]+n.x[1])/2,n.y[0]-25,(n.z[0]+n.z[1])/2),a.raycast=()=>{},t.add(a),_.current=a},[c,i,C]);let se=(0,I.useMemo)(()=>({active:o,dim:A,text:M,hoverId:v,selectedId:a}),[o,A,M,v,a]),le=(0,I.useRef)(se);(0,I.useEffect)(()=>{le.current=se,pr(e,p.current,se)},[e,se]);let ue=(0,I.useCallback)(e=>{let t=new he({color:e.color,roughness:.5,metalness:.1,transparent:!0,opacity:.95}),n=new O(new k(1,20,14),t),r=new Ce(e.label,7,`#18181b`);r.fontFace=`Pretendard, 'Apple SD Gothic Neo', 'Noto Sans KR', system-ui, sans-serif`,r.backgroundColor=!1,r.material.depthWrite=!1,r.material.transparent=!0,r.material.fog=!1;let i=new ae,a=new De;r.onBeforeRender=(e,t,n)=>{r.getWorldPosition(i);let o=n.position.distanceTo(i),s=h.current;r.material.opacity=r.userData.forced?1:or(o,s),s>0&&a.scale.setScalar(Math.min(1.6,Math.max(.6,o/s)))},a.add(r);let o=new De;o.add(n,a);let s=null;return e.ring&&(s=new O(new ne(1.55,.14,8,40),new D({color:Ae,transparent:!0,opacity:.95})),s.onBeforeRender=(e,t,n)=>{s.quaternion.copy(n.quaternion)},s.raycast=()=>{},o.add(s)),p.current.set(e.id,{group:o,mesh:n,holder:a,label:r,ring:s}),pr([e],p.current,le.current),o},[]),fe=(0,I.useCallback)(e=>{if(e.style!==`dashed`)return null;let t=new de;t.setAttribute(`position`,new Te(new Float32Array(6),3));let n=new Ee({color:e.color??`#8b5cf6`,dashSize:2.5,gapSize:1.8,transparent:!0,opacity:.9});return new re(t,n)},[]),pe=(0,I.useCallback)((e,{start:t,end:n},r)=>{if(r.style!==`dashed`)return!1;let i=e,a=i.geometry.getAttribute(`position`);return a.setXYZ(0,t.x,t.y,t.z),a.setXYZ(1,n.x,n.y,n.z),a.needsUpdate=!0,i.geometry.computeBoundingSphere(),i.computeLineDistances(),!0},[]);(0,I.useEffect)(()=>{fr(t,o,A)},[t,o,A]),(0,I.useEffect)(()=>{let e=()=>{document.hidden?f.current?.pauseAnimation():f.current?.resumeAnimation()};return document.addEventListener(`visibilitychange`,e),()=>document.removeEventListener(`visibilitychange`,e)},[]);let F=(0,I.useCallback)(e=>{if(!o)return!0;let[t,n]=je(e);return o.has(t)&&o.has(n)},[o]),me=(0,I.useCallback)(e=>F(e)?e.color??j:A,[F,j,A]),_e=(0,I.useCallback)(e=>o&&a&&F(e)?1.2:e.style===`evidence`?.3:.6,[o,a,F]),ve=(0,I.useCallback)(e=>F(e)?j:A,[F,j,A]),ye=(0,I.useCallback)(e=>b(e.label),[]),xe=(0,I.useCallback)(e=>b(e.label),[]),Se=(0,I.useCallback)(e=>e.style===`arrow`?4:0,[]),we=(0,I.useCallback)(e=>e.particles,[]),ke=(0,I.useCallback)(e=>e.color??j,[j]),Me=(0,I.useCallback)(e=>ee(e?.id??null),[]),Ne=(0,I.useCallback)((e,t)=>l(e,t),[l]);return(0,cr.jsx)(rr,{ref:f,graphData:T,width:n,height:r,backgroundColor:E,showNavInfo:!1,controlType:`orbit`,nodeId:`id`,nodeVal:`val`,nodeRelSize:lr,nodeLabel:ye,nodeThreeObject:ue,linkColor:me,linkOpacity:.6,linkWidth:_e,linkLabel:xe,linkThreeObject:fe,linkThreeObjectExtend:!1,linkPositionUpdate:pe,linkDirectionalArrowLength:Se,linkDirectionalArrowRelPos:1,linkDirectionalArrowColor:ve,linkDirectionalParticles:we,linkDirectionalParticleWidth:1.6,linkDirectionalParticleSpeed:.006,linkDirectionalParticleColor:ke,warmupTicks:60,cooldownTicks:200,cooldownTime:6e3,onEngineStop:ie,onNodeClick:Ne,onNodeHover:Me,onBackgroundClick:u})}export{mr as default};