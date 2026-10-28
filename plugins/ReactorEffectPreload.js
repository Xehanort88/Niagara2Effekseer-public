//=============================================================================
// ReactorEffectPreload.js
//=============================================================================
/*:
 * @target MZ
 * @plugindesc Loads the battle's Effekseer animations while the battle scene
 * opens, so the first cast of a heavy effect does not freeze the game.
 * @author Niagara2Effseeker
 *
 * @param maxWaitMs
 * @text Longest wait (ms)
 * @type number
 * @min 0
 * @default 3000
 * @desc The battle starts after this long even if some effects are still
 * loading. Those finish in the background, as they would without the plugin.
 *
 * @param skills
 * @text Party skills and attacks
 * @type boolean
 * @default true
 *
 * @param items
 * @text Party items usable in battle
 * @type boolean
 * @default true
 *
 * @param enemySkills
 * @text Troop enemies' actions
 * @type boolean
 * @default true
 *
 * @param troopEvents
 * @text Show Animation commands in the troop's battle events
 * @type boolean
 * @default true
 *
 * @param extraIds
 * @text Always preload these animation IDs
 * @type string
 * @default
 * @desc Comma-separated animation IDs, e.g. 317,318.
 *
 * @param log
 * @text Log to console
 * @type boolean
 * @default false
 *
 * @help
 * The first time an Effekseer effect plays, the runtime decodes its textures,
 * uploads them to the GPU and compiles its material shaders on the main thread.
 * A large effect stops the game for that long, in the middle of the spell.
 *
 * This plugin starts those loads when the battle scene is created. The scene
 * already waits for every effect in the cache before it starts, so the cost is
 * paid behind the battle transition instead. Effects stay cached until the next
 * map transfer clears them (on the overlay; see below), so later battles on the
 * same map start at once.
 *
 * Only animations with an Effekseer effect are loaded (sheet animations are
 * skipped). Each file is checked first, because the Effekseer web runtime never
 * reports a missing effect file (it treats the server's 404 page as the file).
 * A missing or broken effect is skipped quietly; playing it behaves exactly as
 * it does without this plugin.
 * Effects drawn inside a 3D battle room load through their own path and are not
 * preloaded.
 *
 * On RPG Reactor runtime 20261027.3 or later, screen animations play inside the
 * battle scene in their own Effekseer context; the effects load there (the
 * engine's in-scene layer), where the cast looks for them. Earlier runtimes, and
 * a project with Graphics.effekseerInScene = false, load into the overlay as
 * before. Effects in the in-scene layer stay loaded for the whole session.
 *
 * No commands. Measured with tools/ingame in Niagara2Effseeker (README).
 */

(() => {
    "use strict";
    const pluginName = "ReactorEffectPreload";
    const p = PluginManager.parameters(pluginName);
    const bool = (v, d) => (v === undefined || v === "" ? d : String(v) === "true");
    const params = {
        maxWaitMs: Number(p.maxWaitMs || 3000),
        skills: bool(p.skills, true),
        items: bool(p.items, true),
        enemySkills: bool(p.enemySkills, true),
        troopEvents: bool(p.troopEvents, true),
        extraIds: String(p.extraIds || "").split(",").map((s) => Number(s.trim())).filter((n) => n > 0),
        log: bool(p.log, false),
    };

    const EffectPreload = {
        params,
        enabled: true,
        last: null, // { requested: [...], failed: [...], startedMs, readyMs, timedOut }
    };
    window.ReactorEffectPreload = EffectPreload;

    function animationIdsForAction(item, subject, out) {
        if (!item) return;
        const id = item.animationId;
        if (id > 0) out.add(id);
        else if (id < 0 && subject && subject.isActor && subject.isActor()) {
            out.add(subject.attackAnimationId1());
            out.add(subject.attackAnimationId2());
        }
    }

    function collectAnimationIds() {
        const ids = new Set(params.extraIds);
        if (params.skills) {
            for (const actor of $gameParty.battleMembers()) {
                animationIdsForAction($dataSkills[actor.attackSkillId()], actor, ids);
                animationIdsForAction($dataSkills[actor.guardSkillId()], actor, ids);
                for (const skill of actor.skills()) animationIdsForAction(skill, actor, ids);
            }
        }
        if (params.items) {
            const leader = $gameParty.leader();
            for (const item of $gameParty.items()) {
                if (item && (item.occasion === 0 || item.occasion === 1)) animationIdsForAction(item, leader, ids);
            }
        }
        if (params.enemySkills) {
            for (const enemy of $gameTroop.members()) {
                const data = enemy.enemy();
                for (const action of (data && data.actions) || []) {
                    animationIdsForAction($dataSkills[action.skillId], enemy, ids);
                }
            }
        }
        if (params.troopEvents) {
            const troop = $gameTroop.troop();
            for (const page of (troop && troop.pages) || []) {
                for (const command of page.list || []) {
                    if (command.code === 212 && command.parameters[1] > 0) ids.add(command.parameters[1]);
                }
            }
        }
        ids.delete(0);
        return [...ids];
    }

    // The Effekseer web runtime treats any HTTP response as a loaded file, so a
    // missing effect never calls onerror; it just never finishes loading. Check
    // the file first, so a missing one is skipped instead of holding the battle.
    function fileExists(url, callback) {
        const xhr = new XMLHttpRequest();
        xhr.open("GET", url, true);
        xhr.responseType = "arraybuffer";
        xhr.onload = () => callback(xhr.status === 200 || (xhr.status === 0 && !!xhr.response && xhr.response.byteLength > 0));
        xhr.onerror = () => callback(false);
        xhr.send(null);
    }

    // Since RPG Reactor runtime 20261027.3 screen animations play inside PIXI's
    // scene, in their own Effekseer context (Sprite_Animation.SceneLayer), and
    // never read EffectManager._cache: preload there while that path is in use.
    function sceneLayer() {
        return typeof EffectManager.sceneLayer === "function" ? EffectManager.sceneLayer() : null;
    }

    function isCached(name, url) {
        const layer = sceneLayer();
        return layer ? layer.cache.has(name) : !!EffectManager._cache[url];
    }

    function startLoad(record, name, url) {
        const layer = sceneLayer();
        if (layer) {
            // The layer gives a load it lets go of after EffectManager.PRELOAD_WAIT_MS
            // to Scene_Base.isReady, as the engine's own map preload does.
            if (layer.preload(name)) record.requested.push(name);
            return;
        }
        if (EffectManager._cache[url]) return;
        // Our own load, so a failure is not queued as a game error: the
        // effect is dropped from the cache and the battle carries on.
        const effect = Graphics.effekseer.loadEffect(url, 1, () => {}, () => {
            if (EffectManager._cache[url] === effect) delete EffectManager._cache[url];
            record.failed.push(name);
            if (params.log) console.warn(`[${pluginName}] could not preload ${name}`);
        });
        EffectManager._cache[url] = effect;
        record.requested.push(name);
    }

    function preload() {
        const record = { requested: [], failed: [], startedMs: performance.now(), readyMs: null, timedOut: false, checking: 0 };
        EffectPreload.last = record;
        if (!EffectPreload.enabled || !Graphics.effekseer) return record;
        for (const id of collectAnimationIds()) {
            const animation = $dataAnimations[id];
            const name = animation && animation.effectName;
            if (!name) continue;
            const url = EffectManager.makeUrl(name);
            if (isCached(name, url)) continue;
            record.checking++;
            fileExists(url, (exists) => {
                record.checking--;
                if (exists) startLoad(record, name, url);
                else {
                    record.failed.push(name);
                    if (params.log) console.warn(`[${pluginName}] effect file not found: ${url}`);
                }
            });
        }
        if (params.log) console.log(`[${pluginName}] checking ${record.checking} effect(s)`);
        return record;
    }

    const _Scene_Battle_create = Scene_Battle.prototype.create;
    Scene_Battle.prototype.create = function() {
        _Scene_Battle_create.call(this);
        this._effectPreload = preload();
    };

    // Scene_Base.isReady already waits for every cached effect. Cap that wait,
    // so a slow disk can never hold the battle for longer than maxWaitMs.
    const _Scene_Battle_isReady = Scene_Battle.prototype.isReady;
    Scene_Battle.prototype.isReady = function() {
        const record = this._effectPreload;
        const ready = _Scene_Battle_isReady.call(this) && !(record && record.checking > 0);
        if (!record || record.readyMs !== null) return ready || !!(record && record.timedOut);
        if (ready) {
            record.readyMs = performance.now() - record.startedMs;
            if (params.log) console.log(`[${pluginName}] ready after ${Math.round(record.readyMs)} ms`);
            return true;
        }
        if (performance.now() - record.startedMs > params.maxWaitMs && ImageManager.isReady() && FontManager.isReady()) {
            record.readyMs = performance.now() - record.startedMs;
            record.timedOut = true;
            if (params.log) console.warn(`[${pluginName}] starting the battle before every effect loaded`);
            return true;
        }
        return false;
    };
})();
