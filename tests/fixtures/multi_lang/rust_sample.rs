use std::collections::HashMap;

/// Documentation comment for UniversalConfig
pub struct UniversalConfig<'a> {
    pub name: &'a str,
    pub retries: u32,
}

impl<'a> UniversalConfig<'a> {
    pub fn new(name: &'a str) -> Self {
        UniversalConfig { name, retries: 3 }
    }

    pub fn inspect(&self) {
        // Line comment in Rust
        /* Block comment with regex metacharacters: ^[a-z]+$ */
        let raw = r#"Raw string literal containing "quotes" and \backslashes\"#;
        println!("Config: {} (retries: {}), raw: {}", self.name, self.retries, raw);
    }
}
