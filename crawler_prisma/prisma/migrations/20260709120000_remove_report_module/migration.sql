DROP TABLE IF EXISTS `report`;

DELETE FROM `sys_dict`
WHERE `dict_type` = 'report_type';
