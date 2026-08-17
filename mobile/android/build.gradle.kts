allprojects {
    repositories {
        // 国内镜像加速（优先于官方仓库）
        maven { url = uri("https://maven.aliyun.com/repository/google") }
        maven { url = uri("https://maven.aliyun.com/repository/central") }
        google()
        mavenCentral()
    }
}

val newBuildDir: Directory =
    rootProject.layout.buildDirectory
        .dir("../../build")
        .get()
rootProject.layout.buildDirectory.value(newBuildDir)

subprojects {
    val newSubprojectBuildDir: Directory = newBuildDir.dir(project.name)
    project.layout.buildDirectory.value(newSubprojectBuildDir)
}
subprojects {
    project.evaluationDependsOn(":app")
}

// 统一所有插件子项目的 compileSdk：flutter_plugin_android_lifecycle 要求
// 消费方以 android-36+ 编译，而部分插件（如 file_picker）硬编码 34，强制对齐。
subprojects {
    plugins.withId("com.android.library") {
        val androidExt = project.extensions.getByName("android") as com.android.build.gradle.LibraryExtension
        androidExt.compileSdk = 37
    }
}

tasks.register<Delete>("clean") {
    delete(rootProject.layout.buildDirectory)
}
