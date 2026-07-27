-- Source: /home/intsig/【优先爬取网站】项目爬取网站清单.xlsx
-- Scope: the 28 rows highlighted as priority in the workbook.
-- Only sources with an implemented Spider are enabled initially:
-- huaxin, jiubang, sxzwfw. Other sources are registered but disabled.
-- source_level preserves the workbook's "类型" value; crawl_config is NULL.

USE `crawler`;

START TRANSACTION;

INSERT INTO `data_source` (
    `name`,
    `short_code`,
    `base_url`,
    `source_level`,
    `province`,
    `crawl_frequency_minutes`,
    `crawl_config`,
    `is_enabled`,
    `created_at`,
    `updated_at`
) VALUES
(
    '中国采购与招标网', 'chinabidding', 'https://www.chinabidding.com.cn/',
    '国家', NULL, 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '中国政府采购网', 'ccgp', 'https://www.ccgp.gov.cn/',
    '国家', NULL, 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '中国招标投标公共服务平台', 'cebpubservice', 'http://www.cebpubservice.com/',
    '国家', NULL, 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '全国公共资源交易平台', 'ggzy', 'https://www.ggzy.gov.cn/',
    '国家', NULL, 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '中国招标投标协会', 'ctba', 'http://www.ctba.org.cn/',
    '国家', NULL, 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '华新阳光采购平台', 'ygcgpt', 'https://www.ygcgpt.com/',
    '企业', '山西', 60,
    NULL,
    TRUE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '山西焦煤电子招采平台', 'sxccdzzcpt', 'https://www.sxccdzzcpt.cn/home/homePage',
    '企业', '山西', 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '煤婆电子招投标平台', 'mp12345', 'https://www.mp12345.com/',
    '企业', NULL, 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '山西交通控股集团有限公司电子招投标采购交易平台', 'sxjkzcpt', 'https://www.sxjkzcpt.com.cn/pub/index_pages.html',
    '企业', '山西', 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '云买卖电子综合交易平台', 'eqbidding', 'https://www.eqbidding.com/',
    '三方', NULL, 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '伟拓招标采购交易平台', 'wtjypt', 'http://www.wtjypt.com/',
    '三方', NULL, 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '比比网电子招标投标交易平台', 'bitbid', 'http://www.bitbid.cn/',
    '三方', NULL, 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '赢标电子招标采购交易平台（山西专区）', 'shanxi_fzbidding', 'http://shanxi.fzbidding.com/home',
    '三方', '山西', 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '玖邦招标采购电子交易平台', 'bjjbkj', 'https://www.bjjbkj.cn/',
    '三方', '山西', 60,
    NULL,
    TRUE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '旺采网山西交易平台', '5ibid_shanxi', 'https://www.5ibid.net/Liems/index.html',
    '三方', '山西', 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '中招联合（山西）招标采购网', 'shanxi_365trade', 'http://shanxi.365trade.com.cn/',
    '三方', '山西', 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '易招标山西交易平台', 'sxty_ebidding', 'http://sxty.ebidding.net.cn/',
    '三方', '山西', 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '山西新点招投标交易平台', 'sxxindian', 'http://www.sxxindian.com/',
    '三方', '山西', 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '全国公共资源交易平台（山西省·临汾市）', 'linfen_ggzy', 'http://lfggzyjy.linfen.gov.cn/',
    '省市', '山西', 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '全国公共资源交易平台（山西省·太原市）', 'taiyuan_ggzy', 'https://ggzy.xzspglj.taiyuan.gov.cn/',
    '省市', '山西', 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '山西省公共资源交易平台', 'sxzwfw', 'https://prec.sxzwfw.gov.cn/',
    '省市', '山西', 60,
    NULL,
    TRUE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '山西省招标投标公共服务平台', 'sxbid', 'https://www.sxbid.com.cn/f/new/jypt',
    '省市', '山西', 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '润世和电子招投标交易平台', 'runshihua', 'https://ec.runshihua.com/web/home',
    '三方', NULL, 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '千极数采电子交易平台', 'qianjilink', 'https://www.qianjilink.com/',
    '三方', NULL, 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '全国公共资源交易平台（山西省·吕梁市）', 'lvliang_ggzy', 'http://ggzyjyzx.lvliang.gov.cn/',
    '省市', '山西', 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '国信e采（山西）', 'guoxin_shanxi', 'https://gx.e-bidding.org/',
    '企业', '山西', 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '山西省政府采购网', 'ccgp_shanxi', 'http://www.ccgp-shanxi.gov.cn/',
    '政府采购', '山西', 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
),
(
    '山西省招投标协会', 'sxtba', 'https://sxtba.com/home',
    '协会网站', '山西', 60,
    NULL,
    FALSE, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
)
ON DUPLICATE KEY UPDATE
    `name` = VALUES(`name`),
    `base_url` = VALUES(`base_url`),
    `source_level` = VALUES(`source_level`),
    `province` = VALUES(`province`),
    `crawl_frequency_minutes` = VALUES(`crawl_frequency_minutes`),
    `crawl_config` = VALUES(`crawl_config`),
    `updated_at` = CURRENT_TIMESTAMP(3);

COMMIT;

-- Verification query (read-only):
SELECT
    `id`, `name`, `short_code`, `base_url`, `source_level`, `province`, `is_enabled`
FROM `data_source`
WHERE `short_code` IN (
    'chinabidding', 'ccgp', 'cebpubservice', 'ggzy_national', 'ctba',
    'huaxin', 'sxccdzzcpt', 'mp12345', 'sxjkzcpt', 'eqbidding',
    'wtjypt', 'bitbid', 'shanxi_fzbidding', 'jiubang', 'ibid5_shanxi',
    'shanxi_365trade', 'sxty_ebidding', 'sxxindian', 'linfen_ggzy',
    'taiyuan_ggzy', 'sxzwfw', 'sxbid', 'runshihua', 'qianjilink',
    'lvliang_ggzy', 'guoxin_shanxi', 'ccgp_shanxi', 'sxtba'
)
ORDER BY `id`;
