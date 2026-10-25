/** 价格表与成本估算：控制台和用量抽屉共用一份，避免两处价格漂移。
 *  每百万 token 的人民币单价。以模型官网为准，换了模型记得改这里。
 *  hit 是缓存命中部分的输入价，miss 是未命中部分——两者通常差一个数量级。
 *  key 不带 litellm 的 "openai/" 前缀，匹配时统一剥掉。 */

export const PRICE_PER_M: Record<string, { hit: number; miss: number; out: number }> = {
  "deepseek-chat": { hit: 0.2, miss: 2, out: 3 },
  "deepseek-reasoner": { hit: 0.2, miss: 2, out: 8 },
  "deepseek-v4-flash": { hit: 0.2, miss: 2, out: 3 },
};

export function estimateCost(
  model: string,
  cacheHitTokens: number,
  cacheMissTokens: number,
  completionTokens: number,
): number | null {
  const short = model.split("/").pop() ?? model;
  const price = PRICE_PER_M[short];
  if (!price) return null;
  return (
    (cacheHitTokens / 1e6) * price.hit +
    (cacheMissTokens / 1e6) * price.miss +
    (completionTokens / 1e6) * price.out
  );
}

export function fmtTokens(n: number): string {
  return n >= 10000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}
