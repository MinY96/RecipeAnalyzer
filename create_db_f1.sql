-- 1. dm_create_master_recipe_info
-- f1.dm_create_master_recipe_info definition
CREATE TABLE f1.dm_create_master_recipe_info (
	tech_prod varchar(10) NOT NULL,
	tech varchar(10) NOT NULL,
	prod_group varchar(15) NOT NULL,
	"level" varchar(10) NOT NULL,
	layer varchar(15) NOT NULL,
	"mode" varchar(10) NOT NULL,
	CONSTRAINT master_create_pk PRIMARY KEY (tech_prod, tech, prod_group, level, layer, mode)
);


-- 2. dm_master_recipe_info
-- f1.dm_master_recipe_info definition
CREATE TABLE f1.dm_master_recipe_info (
	master_key varchar(15) NOT NULL,
	tech_prod varchar(10) NULL,
	tech varchar(10) NULL,
	prod_group varchar(15) NULL,
	"level" varchar(10) NULL,
	layer varchar(15) NULL,
	"mode" varchar(10) NULL,
	model varchar(50) NOT NULL,
	CONSTRAINT dm_master_recipe_info_pkey PRIMARY KEY (master_key)
);
CREATE INDEX ix_dm_master_recipe_info_model ON f1.dm_master_recipe_info USING btree (model);


-- 3. dm_matching_rate
-- f1.dm_matching_rate definition
CREATE TABLE f1.dm_matching_rate (
	model varchar(15) NULL,
	equip varchar(10) NOT NULL,
	recipe int4 NULL,
	matching int4 NULL,
	non_matching int4 NULL,
	no_master int4 NULL,
	exclude_matching int4 NULL,
	matching_rate float4 NULL,
	aggregation_time varchar(30) NOT NULL,
	CONSTRAINT dm_matching_rate_pkey PRIMARY KEY (equip, aggregation_time)
);


-- 4. dm_para_type
-- f1.dm_para_type definition
CREATE TABLE f1.dm_para_type (
	para_nm varchar(30) NOT NULL,
    model varchar(15) NOT NULL,
	para_type varchar(20) NULL,
	CONSTRAINT dm_para_type_pkey PRIMARY KEY (model, para_nm)
);


-- 5. dm_program_usage_history
-- f1.dm_program_usage_history definition
CREATE TABLE f1.dm_program_usage_history (
	session_id uuid NOT NULL,
	fab varchar(20) NOT NULL,
	user_id varchar(100) NOT NULL,
	display_name varchar(200) NULL,
	user_role varchar(50) NULL,
	pc_name varchar(255) NOT NULL,
	os_user varchar(255) NULL,
	os_version text NULL,
	process_architecture varchar(50) NULL,
	app_version varchar(50) NULL,
	client_ip inet NULL,
	login_time timestamptz NOT NULL,
	logout_time timestamptz NULL,
	CONSTRAINT ck_dm_program_usage_history_time CHECK (((logout_time IS NULL) OR (logout_time >= login_time))),
	CONSTRAINT dm_program_usage_history_pkey PRIMARY KEY (session_id)
);
CREATE INDEX ix_dm_program_usage_history_login_time ON f1.dm_program_usage_history USING btree (login_time DESC);
CREATE INDEX ix_dm_program_usage_history_pc_login ON f1.dm_program_usage_history USING btree (pc_name, login_time DESC);
CREATE INDEX ix_dm_program_usage_history_user_login ON f1.dm_program_usage_history USING btree (user_id, login_time DESC);


-- 6. dm_recipe_matching_rate
-- f1.dm_recipe_matching_rate definition
CREATE TABLE f1.dm_recipe_matching_rate (
	recipe int4 NULL,
	single int4 NULL,
	multi int4 NULL,
	matching int4 NULL,
	non_matching int4 NULL,
	matching_rate float4 NULL,
	aggregation_time varchar(30) NOT NULL,
	CONSTRAINT dm_recipe_matching_rate_pkey PRIMARY KEY (aggregation_time)
);


-- 7. dm_update_history
-- f1.dm_update_history definition
CREATE TABLE f1.dm_update_history (
	model varchar(15) NULL,
	equip varchar(10) NOT NULL,
	new_rcp int4 NULL,
	update_rcp int4 NULL,
	remove_rcp int4 NULL,
	aggregation_time varchar(30) NOT NULL,
	CONSTRAINT dm_update_history_pkey PRIMARY KEY (equip, aggregation_time)
);


-- 8. dm_master_recipe_para
-- f1.dm_master_recipe_para definition
CREATE TABLE f1.dm_master_recipe_para (
	master_key varchar(15) NOT NULL,
	para_nm varchar(30) NOT NULL,
	para_value varchar(20) NULL,
	CONSTRAINT master_recipe_para_pk PRIMARY KEY (master_key, para_nm),
	CONSTRAINT fk_master_recipe_para_key FOREIGN KEY (master_key) REFERENCES f1.dm_master_recipe_info(master_key) ON DELETE CASCADE
)
PARTITION BY HASH (master_key);
-- f1.master_rcp_para_p00 definition
CREATE TABLE f1.master_rcp_para_p00 PARTITION OF f1.dm_master_recipe_para  FOR VALUES WITH (modulus 4, remainder 0);

-- f1.master_rcp_para_p01 definition
CREATE TABLE f1.master_rcp_para_p01 PARTITION OF f1.dm_master_recipe_para  FOR VALUES WITH (modulus 4, remainder 1);

-- f1.master_rcp_para_p02 definition
CREATE TABLE f1.master_rcp_para_p02 PARTITION OF f1.dm_master_recipe_para  FOR VALUES WITH (modulus 4, remainder 2);

-- f1.master_rcp_para_p03 definition
CREATE TABLE f1.master_rcp_para_p03 PARTITION OF f1.dm_master_recipe_para  FOR VALUES WITH (modulus 4, remainder 3);


-- 9. dm_recipe_info
-- f1.dm_recipe_info definition
CREATE TABLE f1.dm_recipe_info (
	recipe_key varchar(30) NOT NULL,
	model varchar(15) NULL,
	equip varchar(10) NULL,
	device varchar(10) NULL,
	recipe varchar(15) NULL,
	folder_path varchar(200) NULL,
	file_path varchar(200) NULL,
	dt_edit_time varchar(30) NULL,
	backup_folder_path varchar(200) NULL,
	backup_file_path varchar(200) NULL,
	dt_backup varchar(30) NULL,
	status varchar(10) NULL,
	is_remove bool DEFAULT false NULL,
	master_key varchar(15) NULL,
	tech_prod varchar(10) NULL,
	tech varchar(10) NULL,
	prod_group varchar(15) NULL,
	"level" varchar(10) NULL,
	layer varchar(15) NULL,
	"mode" varchar(10) NULL,
	matching bool NULL,
	exclude_matching bool DEFAULT false NULL,
	exclude_code varchar(30) NULL,
	CONSTRAINT dm_recipe_info_pkey PRIMARY KEY (recipe_key),
	CONSTRAINT fk_master_recipe_key FOREIGN KEY (master_key) REFERENCES f1.dm_master_recipe_info(master_key) ON DELETE CASCADE
);


-- 10. dm_recipe_para
-- f1.dm_recipe_para definition
CREATE TABLE f1.dm_recipe_para (
	recipe_key varchar(30) NOT NULL,
	para_nm varchar(30) NOT NULL,
	para_value varchar(20) NULL,
	matching bool NULL,
	CONSTRAINT recipe_para_pk PRIMARY KEY (recipe_key, para_nm),
	CONSTRAINT fk_recipe_para_key FOREIGN KEY (recipe_key) REFERENCES f1.dm_recipe_info(recipe_key) ON DELETE CASCADE
)
PARTITION BY HASH (recipe_key);
-- f1.rcp_para_p00 definition
CREATE TABLE f1.rcp_para_p00 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 0);

-- f1.rcp_para_p01 definition
CREATE TABLE f1.rcp_para_p01 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 1);

-- f1.rcp_para_p02 definition
CREATE TABLE f1.rcp_para_p02 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 2);

-- f1.rcp_para_p03 definition
CREATE TABLE f1.rcp_para_p03 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 3);

-- f1.rcp_para_p04 definition
CREATE TABLE f1.rcp_para_p04 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 4);

-- f1.rcp_para_p05 definition
CREATE TABLE f1.rcp_para_p05 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 5);

-- f1.rcp_para_p06 definition
CREATE TABLE f1.rcp_para_p06 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 6);

-- f1.rcp_para_p07 definition
CREATE TABLE f1.rcp_para_p07 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 7);

-- f1.rcp_para_p08 definition
CREATE TABLE f1.rcp_para_p08 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 8);

-- f1.rcp_para_p09 definition
CREATE TABLE f1.rcp_para_p09 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 9);

-- f1.rcp_para_p10 definition
CREATE TABLE f1.rcp_para_p10 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 10);

-- f1.rcp_para_p11 definition
CREATE TABLE f1.rcp_para_p11 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 11);

-- f1.rcp_para_p12 definition
CREATE TABLE f1.rcp_para_p12 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 12);

-- f1.rcp_para_p13 definition
CREATE TABLE f1.rcp_para_p13 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 13);

-- f1.rcp_para_p14 definition
CREATE TABLE f1.rcp_para_p14 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 14);

-- f1.rcp_para_p15 definition
CREATE TABLE f1.rcp_para_p15 PARTITION OF f1.dm_recipe_para  FOR VALUES WITH (modulus 16, remainder 15);