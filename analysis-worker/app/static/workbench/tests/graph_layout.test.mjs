import test from 'node:test';
import assert from 'node:assert/strict';
import {createGraphLayout,stepGraphLayout,settleGraphLayout,graphLabelPlacements,createGraphMotion,advanceGraphMotion,graphMotionStepLimit} from '../graph_layout.js';
const node = key => ({account_key:key,name:'账号名称'+key});
const edge = (a,b) => ({source_key:a,target_key:b,relationship:'follows'});

test('dragging pushes colliding neighbours and keeps the held node and source evidence intact',()=>{
  const data={nodes:[node('a'),node('b'),node('c')],edges:[edge('a','b')]};
  const layout=createGraphLayout(data,new Map([['a',{x:400,y:300}],['b',{x:401,y:300}],['c',{x:500,y:300}]]));
  stepGraphLayout(layout,{held:'a'});
  assert.equal(layout.byKey.get('a').x,400); assert.equal(layout.byKey.get('a').y,300);
  assert.ok(layout.byKey.get('b').x>425); assert.equal(data.edges.length,1); assert.equal(data.nodes[0].x,undefined);
});
test('explicit pins survive settling and new neighbouring discoveries',()=>{
  const layout=settleGraphLayout(createGraphLayout({nodes:[node('a'),node('b')],edges:[edge('a','b')]},new Map([['a',{x:420,y:260,pinned:true}]])));
  assert.equal(layout.byKey.get('a').x,420); assert.equal(layout.byKey.get('a').y,260);
  assert.ok(Math.hypot(layout.byKey.get('b').x-420,layout.byKey.get('b').y-260)>35);
});
test('dense networks remain finite and relationship types do not multiply physical attraction',()=>{
  const nodes=Array.from({length:80},(_,i)=>node('n'+i));
  const edges=nodes.slice(1).flatMap(n=>[edge('n0',n.account_key),{...edge('n0',n.account_key),relationship:'mentions'}]);
  const layout=settleGraphLayout(createGraphLayout({nodes,edges}),80);
  assert.equal(layout.springs.length,79); assert.equal(layout.byKey.get('n0').degree,79);
  assert.ok(layout.nodes.every(n=>Number.isFinite(n.x)&&Number.isFinite(n.y)&&Math.abs(n.x)<5000&&Math.abs(n.y)<5000));
});
test('coincident nodes separate deterministically during a direct gesture',()=>{
  const data={nodes:[node('a'),node('b')],edges:[]}, saved=new Map([['a',{x:400,y:300}],['b',{x:400,y:300}]]);
  const a=createGraphLayout(data,saved),b=createGraphLayout(data,saved); stepGraphLayout(a,{held:'a'});stepGraphLayout(b,{held:'a'});
  assert.deepEqual(a.nodes,b.nodes); assert.ok(Math.hypot(a.nodes[0].x-a.nodes[1].x,a.nodes[0].y-a.nodes[1].y)>30);
});
test('dense labels are bounded when zoomed out and retain the selected account',()=>{
  const layout=settleGraphLayout(createGraphLayout({nodes:Array.from({length:60},(_,i)=>node('n'+i)),edges:[]}));
  const labels=graphLabelPlacements(layout.nodes,.4,{selected:'n59'}); assert.ok(labels.has('n59'));assert.ok(labels.size<=9);
  const ordinary=[...labels.entries()].filter(([key])=>key!=='n59').map(([,v])=>v.bounds);
  for(let i=0;i<ordinary.length;i++)for(let j=i+1;j<ordinary.length;j++){const a=ordinary[i],b=ordinary[j];assert.ok(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y);}
});
test('collision projections are measured even with no force or velocity',()=>{
  const layout=createGraphLayout({nodes:[node('a'),node('b')],edges:[]},new Map([['a',{x:400,y:300}],['b',{x:400,y:300}]]));
  const movement=stepGraphLayout(layout,{held:'a',alpha:0});
  assert.ok(movement>30); assert.equal(layout.byKey.get('a').x,400); assert.equal(layout.byKey.get('a').y,300);
});
test('moving layouts cannot be declared stable at a frame budget and quiet intervals must be consecutive',()=>{
  const motion=createGraphMotion();
  for(let i=0;i<2000;i++) assert.equal(advanceGraphMotion(motion,.12),false);
  for(let i=0;i<29;i++) assert.equal(advanceGraphMotion(motion,.01),false);
  assert.equal(advanceGraphMotion(motion,.2),false);
  for(let i=0;i<29;i++) assert.equal(advanceGraphMotion(motion,.01,60),false);
  assert.equal(advanceGraphMotion(motion,.01,60),true);
});
test('a brief quiet plateau only begins confirmation and rebound resets physical-step progress',()=>{
  const motion=createGraphMotion();
  for(let i=0;i<30;i++) assert.equal(advanceGraphMotion(motion,.03),false);
  assert.equal(graphMotionStepLimit(motion,144),48);
  assert.equal(motion.quietSteps,30);
  for(let i=0;i<500;i++) assert.equal(advanceGraphMotion(motion,.03),false);
  assert.equal(advanceGraphMotion(motion,.15),false);
  assert.equal(motion.quietSteps,0);assert.equal(graphMotionStepLimit(motion,144),24);
  for(let i=0;i<1799;i++) assert.equal(advanceGraphMotion(motion,.02),false);
  assert.equal(advanceGraphMotion(motion,.02),true);
});
test('sub-threshold renewed acceleration restarts confirmation rather than hiding a slow rebound',()=>{
  const motion=createGraphMotion();
  for(let i=0;i<1799;i++)assert.equal(advanceGraphMotion(motion,.002),false);
  assert.equal(advanceGraphMotion(motion,.012),false);
  assert.equal(motion.quietSteps,1);assert.equal(motion.quiet,1);
  assert.equal(motion.quietMinimumMovement,.012);
  for(let i=0;i<1799;i++)advanceGraphMotion(motion,.01);
  assert.equal(motion.stable,true);
});
test('small per-step motion cannot hide accumulated drift during confirmation',()=>{
  const motion=createGraphMotion(),nodes=[{x:0,y:0}];
  for(let i=0;i<30;i++)advanceGraphMotion(motion,.01,1,nodes);
  for(let i=0;i<2400;i++){nodes[0].x+=.01;assert.equal(advanceGraphMotion(motion,.01,1,nodes),false);}
  assert.ok(motion.confirmationResets>0);assert.ok(motion.confirmationSteps<1800);
  for(let i=0;i<1800;i++)advanceGraphMotion(motion,0,1,nodes);
  assert.equal(motion.stable,true);assert.ok(motion.confirmationDisplacement<=.35);
});
test('the product 144-node grid rejects its transient quiet window and remains quiet after stopping',()=>{
  const nodes=Array.from({length:144},(_,i)=>({account_key:'grid-'+i})),edges=[];
  for(let i=0;i<144;i++) { if(i%12<11)edges.push(edge('grid-'+i,'grid-'+(i+1)));if(i<132)edges.push(edge('grid-'+i,'grid-'+(i+12))); }
  const layout=settleGraphLayout(createGraphLayout({nodes,edges}),90),motion=createGraphMotion();
  let transient=false,rebound=false;
  for(let i=0;i<600000&&!motion.stable;i++) {
    const movement=stepGraphLayout(layout,{alpha:motion.alpha});advanceGraphMotion(motion,movement,1,layout.nodes);
    if(!transient&&motion.quiet>=30) { transient=true;assert.equal(motion.stable,false); }
    if(transient&&movement>.035)rebound=true;
  }
  assert.equal(transient,true);assert.equal(rebound,true);assert.equal(motion.stable,true);
  assert.ok(motion.quietSteps>=1800);
  for(const n of layout.nodes)n.vx=n.vy=0;
  for(let i=0;i<1800;i++)assert.ok(stepGraphLayout(layout,{alpha:motion.alpha})<=.035,'The known plateau must not be accepted as final stability');
});
test('a stationary held node continues pulling actual neighbours and release converges without a cap',()=>{
  const data={nodes:['a','b','c','d'].map(node),edges:[edge('a','b'),edge('b','c'),edge('c','d')]};
  const layout=settleGraphLayout(createGraphLayout(data),90), b=layout.byKey.get('b'), a=layout.byKey.get('a');
  const before={x:b.x,y:b.y}; a.x+=280; a.y+=60;
  for(let i=0;i<60;i++) stepGraphLayout(layout,{held:'a'});
  assert.ok(Math.hypot(b.x-before.x,b.y-before.y)>40);
  const motion=createGraphMotion();
  for(let i=0;i<120000&&!motion.stable;i++) advanceGraphMotion(motion,stepGraphLayout(layout,{alpha:motion.alpha}),1,layout.nodes);
  assert.equal(motion.stable,true,'A simple released chain must reach an actual quiet interval');
  assert.ok(stepGraphLayout(layout,{alpha:motion.alpha})<.035);
  assert.deepEqual(data.edges,[edge('a','b'),edge('b','c'),edge('c','d')]);
});
test('fixed overlapping nodes do not cause an idle infinite solver while free nodes still converge',()=>{
  const layout=createGraphLayout({nodes:['a','b','c'].map(node),edges:[edge('a','c')]},new Map([['a',{x:420,y:260,pinned:true}],['b',{x:420,y:260,pinned:true}]])), motion=createGraphMotion();
  for(let i=0;i<120000&&!motion.stable;i++) advanceGraphMotion(motion,stepGraphLayout(layout,{alpha:motion.alpha}),1,layout.nodes);
  assert.equal(motion.stable,true);
  for(const key of ['a','b']){assert.equal(layout.byKey.get(key).x,420);assert.equal(layout.byKey.get(key).y,260);}
});
