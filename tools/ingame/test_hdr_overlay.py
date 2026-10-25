"""Real-battle HDR plugin smoke/parity test. No game files are changed.
python tools/ingame/test_hdr_overlay.py --work <fresh-folder> --project <game>
"""
import argparse,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'tools/author'),str(ROOT/'tools/niagara')]
from efkauthor import Spec,make_blank,sprite,merge,once
import efkmat_ot0 as EM


def fixture(work):
    work.mkdir(parents=True,exist_ok=True)
    blank=work/'blank.efkefc';make_blank(blank,60)
    tail='\n$F3$ worldPositionOffset = $F3$(0.0,0.0,0.0);\n$F3$ baseColor = $F3$(0.0,0.0,0.0);\n$F1$ metallic=0.0;\n$F1$ roughness=1.0;\n$F1$ ambientOcclusion=1.0;\n$F1$ opacityMask=1.0;\n$F1$ refraction=1.0;'
    for name,value in [('Fixture-hdr',4.0),('Fixture-ldr',0.8)]:
        mat=work/(name+'.efkmat')
        EM.write_material(mat,name,[],('$F3$ emissive = $F3$(%s,0.0,0.0);\n$F1$ opacity=1.0;'%value)+tail)
        s=Spec(base=blank,name=name,end_frame=30)
        settings=merge(sprite('', 'Add',[255]*4),once(30),{'RendererCommonValues/Material':'File',
             'RendererCommonValues/MaterialFile/Path':str(mat),'ScalingValues/Fixed/Scale':[4,4,1]})
        settings.pop('RendererCommonValues/ColorTexture',None)
        s.node0('Fixture',settings);s.build(work/(name+'.efkefc'))
    side={'version':1,'gradeInput':'hdr','grade':'vec3 tmOut = hdr / (hdr + vec3(1.0));',
          'bloom':{'intensity':0.675,'radiusScale':2.4,'sizes':[0.3,1,2,10,30,64],
                   'tints':[0.3465,0.138,0.1176,0.066,0.066,0.061]},'fallback':'Fixture-ldr'}
    (work/'Fixture-hdr.hdr.json').write_text(json.dumps(side))


# Include live plugin stats in the runner's own result, without modifying the runner page or project.
STATS = """
const hdrStates=[];
const hdrGl=Graphics._effekseerGL;
const hdrBlend=hdrGl.blendFuncSeparate.bind(hdrGl);
hdrGl.blendFuncSeparate=function(...args){const key=args.join(',');if(!hdrStates.includes(key))hdrStates.push(key);return hdrBlend(...args);};
const hdrFetch=window.fetch.bind(window);
window.fetch=function(url,opt){if(url==='/__result' && opt){const r=JSON.parse(opt.body);
r.hdrStats=window.ReactorHdrOverlay ? ReactorHdrOverlay.stats : null;r.blendStates=hdrStates;r.glAttributes=hdrGl.getContextAttributes();
opt={...opt,body:JSON.stringify(r)};}return hdrFetch(url,opt);};
"""


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--work',required=True);ap.add_argument('--project',required=True)
    ap.add_argument('--vanilla-id',type=int,default=2)
    ap.add_argument('--port',type=int,default=8960);a=ap.parse_args()
    work=Path(a.work).resolve();project=Path(a.project).resolve();work.mkdir(parents=True,exist_ok=True)
    fixture(work/'fixture')
    # Independent default-context runs have differed by one byte at a few pixels.
    # Control MSAA identically on both sides of the strict screenshot test.
    context_setup = """
(function(){const get=HTMLCanvasElement.prototype.getContext;
HTMLCanvasElement.prototype.getContext=function(kind,options){
 if(kind==='webgl' || kind==='experimental-webgl')options={...options,antialias:false};
 return get.call(this,kind,options);
};})();
"""
    original=(project/'js/reactor_sprites.js').read_text(encoding='utf-8')+'\n'+context_setup
    baseline=work/'reactor_sprites.baseline.js';baseline.write_text(original,encoding='utf-8')
    override=work/'reactor_sprites.override.js'
    override.write_text(original+'\n'+(ROOT/'plugins/ReactorHdrOverlay.js').read_text(),encoding='utf-8')
    deterministic = """
Graphics._effekseerGL.disable(Graphics._effekseerGL.DITHER);
const seedPlay=Graphics.effekseer.play.bind(Graphics.effekseer);
Graphics.effekseer.play=function(...args){const h=seedPlay(...args);h.setRandomSeed(1337);return h;};
Sprite_Animation.prototype.targetPosition=function(){return new Point(640,360);};
// Replace the scene source with a fixed canvas in memory. Stock blit/draw still run unchanged.
const testBackdrop=document.createElement('canvas');testBackdrop.width=1280;testBackdrop.height=720;
const test2d=testBackdrop.getContext('2d');test2d.fillStyle='#202020';test2d.fillRect(0,0,1280,720);
Graphics._canvas=testBackdrop;
Sprite_Animation.prototype.reactor3DScale=function(){return 1;};
"""
    common={'prepare':deterministic,'id':1,'mode':'capture','step':1,'warmup':0,'captureSeconds':15}
    jobs=[dict(common,id=a.vanilla_id,tag='vanilla-off',override={'/js/reactor_sprites.js':str(baseline)}),dict(common,id=a.vanilla_id,tag='vanilla-on',override={'/js/reactor_sprites.js':str(override)}),
          dict(common,tag='hdr-fixture',override={'/js/reactor_sprites.js':str(override)},map={'HdrTest/':str(work/'fixture')},
               prepare=deterministic+STATS+"$dataAnimations[1].effectName='HdrTest/Fixture-hdr';$dataAnimations[1].flashTimings=[];window.REACTOR_HDR={probeScale:8,probeFrame:15};")]
    hdr_job = dict(common, override={'/js/reactor_sprites.js': str(override)}, map={'HdrTest/': str(work/'fixture')})
    select = "$dataAnimations[1].effectName='HdrTest/Fixture-hdr';$dataAnimations[1].flashTimings=[];"
    jobs.append(dict(hdr_job, tag='hdr-fallback', prepare=deterministic+STATS+select+"""
const savedExtension=hdrGl.getExtension.bind(hdrGl);
hdrGl.getExtension=function(name){return /texture_(half_)?float|color_buffer_(half_)?float/.test(name)?null:savedExtension(name);};
"""))
    jobs.append(dict(hdr_job, tag='hdr-resize', prepare=deterministic+STATS+select+"""
const originalHdrRender=Sprite_Animation.renderActive;let resized=false;
Sprite_Animation.renderActive=function(renderer){
 const animations=SceneManager._scene?._spriteset?._animationSprites || [];
 if(!resized && animations.some(s=>s._frameIndex===15)){
  Graphics._effekseerCanvas.width+=2;Graphics._effekseerCanvas.height+=2;resized=true;
 }
 return originalHdrRender.call(this,renderer);
};
"""))
    jf=work/'jobs.json';jf.write_text(json.dumps(jobs,indent=2))
    env=dict(os.environ,MSYS_NO_PATHCONV='1',PYTHONIOENCODING='utf-8')
    p=subprocess.run(['node',str(ROOT/'tools/ingame/run.js'),'--project',str(project),'--out',str(work/'captures'),
                      '--jobs',str(jf),'--port',str(a.port)],env=env)
    compare=subprocess.run(['node',str(ROOT/'tools/ingame/compare.js'),'--out',str(work/'captures'),
                            '--base','vanilla-off','--threshold','0','vanilla-on'],env=env)
    rows=[json.loads(l) for l in (work/'captures/results.jsonl').read_text().splitlines()]
    hdr=next(r for r in rows if r['tag']=='hdr-fixture')
    fallback=next(r for r in rows if r['tag']=='hdr-fallback')
    resize=next(r for r in rows if r['tag']=='hdr-resize')
    cmp=json.loads((work/'captures/compare.json').read_text())
    parity=cmp.get('valid') and cmp['runs']['vanilla-on']['totalPx']==0 and not cmp['runs']['vanilla-on']['framesOnlyInBase'] and not cmp['runs']['vanilla-on']['framesOnlyInRun']
    result={'pixelIdentical':parity,'runnerExit':p.returncode,'compareExit':compare.returncode,'hdr':hdr,'fallback':fallback,'resize':resize,
            'checks':{'noEngineErrors':not hdr.get('engineErrors'),'noConsoleErrors':not hdr.get('console',{}),
                      'ends':hdr.get('capture',{}).get('ended') is True,
                      'hdrAboveOne':hdr.get('hdrStats',{}).get('probe',{}).get('maxLowerBound',0)>1,
                      'fallbackPlayed':fallback.get('capture',{}).get('ended') is True and '/effects/HdrTest/Fixture-ldr.efkefc' in fallback.get('servedEffectFiles',[]) and fallback.get('hdrStats',{}).get('groups')==0 and not fallback.get('engineErrors'),
                      'resizeRecreatedTargets':resize.get('capture',{}).get('ended') is True and resize.get('hdrStats',{}).get('allocations',0)>=2 and not resize.get('console') and not resize.get('engineErrors'),
                      'sharedFrameGrade':hdr.get('hdrStats',{}).get('draws',0)>hdr.get('hdrStats',{}).get('groups',0),
                      'sidecarFetchedOnce':hdr.get('servedEffectFiles',[]).count('/effects/HdrTest/Fixture-hdr.hdr.json')==1}}
    (work/'result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    return int(p.returncode!=0 or compare.returncode!=0 or not parity or not all(result['checks'].values()))

if __name__=='__main__':sys.exit(main())
