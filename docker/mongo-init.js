// Ініціалізація шардованого кластера MongoDB (п. 4.3, таблиці 3.7 і 4.7).
//
// Запускає одноразовий контейнер mongo-init з docker-compose.cluster.yml:
//   mongosh --nodb --quiet /scripts/mongo-init.js
// Повторний запуск безпечний: кожен крок пропускається, якщо вже виконаний.
//
// 1. Набори реплік з одного вузла для сервера конфігурації й трьох шардів
//    (шардований кластер вимагає наборів реплік навіть з одним вузлом).
// 2. Шарди додаються до кластера через маршрутизатор mongos.
// 3. Кожен шард отримує свою зону — діапазон значень shard_key; поле _id
//    у межах зон дорівнює MinKey, наприклад { shard_key: "0342", _id: MinKey }.
// 4. Колекція пригод шардується за складеним ключем { shard_key: 1, _id: 1 }.
//    Зони задано наперед, тож початкові фрагменти одразу створюються на
//    шардах своїх зон і розміщення документів не чекає на балансувальник.
//    Колекцію, уже шардовану за { shard_key: 1 }, переводить на складений
//    ключ команда refineCollectionShardKey.

const CONFIG = { id: "cfg", host: "mongo-config:27019" };
const SHARDS = [
  { id: "shard1", host: "mongo-shard-1:27018", zone: "zone1" },
  { id: "shard2", host: "mongo-shard-2:27018", zone: "zone2" },
  { id: "shard3", host: "mongo-shard-3:27018", zone: "zone3" },
];
const MONGOS = "mongos:27017";
const NAMESPACE = "dnd.adventures";
const SHARD_KEY = { shard_key: 1, _id: 1 };

// Межі зон [нижня, верхня) — ті самі, що обчислює ShardKeyService.zone_bounds()
// у застосунку; їх збіг перевіряє тест tests/unit/test_shard_key.py.
// null означає MinKey або MaxKey.
const ZONE_BOUNDS = [
  [null, "0342"],
  ["0342", "0683"],
  ["0683", null],
];

function bsonMin() {
  try { return MinKey(); } catch (e) { return new MinKey(); }
}

function bsonMax() {
  try { return MaxKey(); } catch (e) { return new MaxKey(); }
}

// Пряме з'єднання з вузлом: до ініціалізації набору реплік вузол ще
// не має первинного, і без directConnection драйвер не обрав би сервер.
function connectWithRetry(host, attempts = 90) {
  for (let i = 1; i <= attempts; i++) {
    try {
      const conn = new Mongo(`mongodb://${host}/?directConnection=true&serverSelectionTimeoutMS=2000`);
      conn.getDB("admin").runCommand({ ping: 1 });
      return conn;
    } catch (e) {
      print(`очікування ${host} (${i}/${attempts}): ${e.message}`);
      sleep(2000);
    }
  }
  throw new Error(`${host} недоступний`);
}

function ok(result, what) {
  if (result.ok !== 1) {
    throw new Error(`${what}: ${JSON.stringify(result)}`);
  }
  return result;
}

function initiate(host, config) {
  const admin = connectWithRetry(host).getDB("admin");
  // mongosh повертає помилку команди винятком, а не документом з ok: 0
  let result;
  try {
    result = admin.runCommand({ replSetInitiate: config });
  } catch (e) {
    result = { ok: 0, codeName: e.codeName, errmsg: e.message };
  }
  if (result.ok === 1) {
    print(`${config._id}: набір реплік створено`);
  } else if (result.codeName === "AlreadyInitialized" || /already initialized/i.test(result.errmsg || "")) {
    print(`${config._id}: набір реплік уже існує`);
  } else {
    throw new Error(`${config._id}: ${JSON.stringify(result)}`);
  }
  for (let i = 0; i < 60; i++) {
    if (admin.runCommand({ hello: 1 }).isWritablePrimary) {
      return;
    }
    sleep(1000);
  }
  throw new Error(`${config._id}: вузол не став первинним`);
}

// 1. Набори реплік
initiate(CONFIG.host, { _id: CONFIG.id, configsvr: true, members: [{ _id: 0, host: CONFIG.host }] });
for (const shard of SHARDS) {
  initiate(shard.host, { _id: shard.id, members: [{ _id: 0, host: shard.host }] });
}

// 2. Шарди в кластері. Маршрутизатор приймає з'єднання лише після того,
// як набір реплік сервера конфігурації став доступним, тому — з очікуванням.
const admin = connectWithRetry(MONGOS).getDB("admin");
const config = admin.getSiblingDB("config");
for (const shard of SHARDS) {
  if (config.shards.findOne({ _id: shard.id })) {
    print(`${shard.id}: уже в кластері`);
  } else {
    ok(admin.runCommand({ addShard: `${shard.id}/${shard.host}`, name: shard.id }), `addShard ${shard.id}`);
    print(`${shard.id}: додано до кластера`);
  }
}

// 3. Зони: шард i зберігає діапазон ZONE_BOUNDS[i]
for (const [i, shard] of SHARDS.entries()) {
  ok(admin.runCommand({ addShardToZone: shard.id, zone: shard.zone }), `addShardToZone ${shard.id}`);
  const [lower, upper] = ZONE_BOUNDS[i];
  if (config.tags.findOne({ ns: NAMESPACE, tag: shard.zone })) {
    print(`${shard.zone}: діапазон уже задано`);
    continue;
  }
  // Межі складеного ключа: значення shard_key і MinKey для _id; верхня межа
  // останньої зони — MaxKey в обох полях
  ok(admin.runCommand({
    updateZoneKeyRange: NAMESPACE,
    min: { shard_key: lower === null ? bsonMin() : lower, _id: bsonMin() },
    max: upper === null ? { shard_key: bsonMax(), _id: bsonMax() } : { shard_key: upper, _id: bsonMin() },
    zone: shard.zone,
  }), `updateZoneKeyRange ${shard.zone}`);
  print(`${shard.zone}: shard_key [${lower ?? "MinKey"}, ${upper ?? "MaxKey"}) → ${shard.id}`);
}

// 4. Колекція пригод. Решта колекцій не шардуються й лишаються
// на первинному шарді бази (п. 4.3).
const sharded = config.collections.findOne({ _id: NAMESPACE, unsplittable: { $ne: true } });
if (!sharded) {
  ok(admin.runCommand({ shardCollection: NAMESPACE, key: SHARD_KEY }), "shardCollection");
  print(`${NAMESPACE}: шардована за { shard_key: 1, _id: 1 }`);
} else if (JSON.stringify(sharded.key) === JSON.stringify({ shard_key: 1 })) {
  // Кластер зі старим ключем лише за shard_key: суфікс _id додається до
  // наявного ключа; потрібен індекс за складеним ключем
  const db = admin.getSiblingDB("dnd");
  ok(db.runCommand({ createIndexes: "adventures", indexes: [{ key: SHARD_KEY, name: "shard_key_1__id_1" }] }),
     "createIndexes");
  ok(admin.runCommand({ refineCollectionShardKey: NAMESPACE, key: SHARD_KEY }), "refineCollectionShardKey");
  print(`${NAMESPACE}: ключ розподілу доповнено до { shard_key: 1, _id: 1 }`);
} else {
  print(`${NAMESPACE}: уже шардована за ${JSON.stringify(sharded.key)}`);
}

const shardIds = [];
for (const shard of config.shards.find().toArray()) {
  shardIds.push(shard._id);
}
print(`кластер готовий: шарди ${shardIds.join(", ")}`);
