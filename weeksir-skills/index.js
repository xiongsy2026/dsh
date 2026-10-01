// weeksir-skills 是"纯技能载体"包：真正内容在 ./skills/<id>/SKILL.md。
// 作为 Cordis bundle 需要一个可加载入口，这里保持无副作用。
// 若发现"bundle 必须提供 client 端注册才加载 skills"，再在此补 apply(ctx)（参考 dsh-image-generation）。
export default {};
